from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

from optionda.agent_view import build_agent_view
from optionda.mailer import (
    MAIL_FILTER_HINT,
    build_message,
    clear_mail,
    delete_thread,
    ensure_session,
    format_list,
    load_session,
    load_smtp,
    pause_session,
    resume_session,
    save_smtp,
    send_desk,
)
from optionda.models import Position, RowMark


def _row() -> RowMark:
    return RowMark(
        position=Position(
            occ_symbol="AVGO261218C00500000",
            underlying="AVGO",
            expiry=date(2026, 12, 18),
            strike=500.0,
            option_type="call",
            qty=1,
            side="long",
            iv_frozen=0.25,
            iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
            entry_premium=3.5,
        ),
        spot=300.0,
        theo=40.0,
        delta=0.2,
        dte=90.0,
        notional=4000.0,
        cost=3.5,
        upnl=200.0,
        close_premium=38.0,
        theo_chg=2.0,
    )


def _view() -> dict:
    return build_agent_view(
        account="main",
        feed="alpaca",
        rows=[_row()],
        realized=10.0,
    )


def test_gmail_filter_xml_skips_primary() -> None:
    from optionda.mailer import gmail_filter_xml

    xml = gmail_filter_xml()
    assert "optionda ·" in xml
    assert "shouldArchive" in xml
    assert 'label\' value=\'optionda\'' in xml or 'name="label" value="optionda"' in xml or "optionda" in xml
    assert "^smartlabel_notification" in xml
    assert "shouldNeverMarkAsImportant" in xml
    assert "not-a-real-password" not in xml


def test_write_gmail_filter_to_home(tmp_path) -> None:
    from optionda.mailer import write_gmail_filter

    path = write_gmail_filter(tmp_path)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "shouldArchive" in text
    assert path.name == "gmail-filter.xml"


def test_smtp_login_roundtrip_and_list_hides_password(tmp_path) -> None:
    save_smtp("devnull@example.com", "not-a-real-password", tmp_path)
    creds = load_smtp(tmp_path)
    assert creds is not None
    assert creds.user == "devnull@example.com"
    assert creds.password == "not-a-real-password"
    session = ensure_session("main", tmp_path)
    shown = format_list(tmp_path)
    assert "devnull@example.com" in shown
    assert "not-a-real-password" not in shown
    assert session.token[:8] in shown
    assert session.token not in shown
    assert "message_id" not in shown
    assert "configured=" not in shown
    assert shown.splitlines()[0].startswith(f"mail  idle  {session.token[:8]}")


def test_list_shows_running_then_idle_after_stop(tmp_path) -> None:
    import os

    from optionda.mailer import stop_mail_worker, write_worker_pid

    save_smtp("devnull@example.com", "not-a-real-password", tmp_path)
    session = ensure_session("main", tmp_path)
    write_worker_pid(os.getpid(), tmp_path)
    shown = format_list(tmp_path)
    assert shown.splitlines()[0].startswith(f"mail  running  {session.token[:8]}")
    assert str(os.getpid()) in shown
    stop_mail_worker(tmp_path)
    after = format_list(tmp_path)
    assert after.splitlines()[0].startswith(f"mail  idle  {session.token[:8]}")
    assert "worker —" in after


def test_build_message_threads_and_looks_like_desk(tmp_path) -> None:
    session = ensure_session("main", tmp_path)
    first = build_message(
        _view(),
        session,
        user="devnull@example.com",
        to_addr="devnull@example.com",
    )
    assert first["Subject"] == f"optionda · main · {session.token[:8]}"
    assert first["From"].startswith("optionda")
    assert first["Auto-Submitted"] == "auto-generated"
    assert "desk.optionda" in first["List-Id"]
    assert first["In-Reply-To"] is None
    assert "<img" not in first.get_body(preferencelist=("html",)).get_content()
    assert "today +" in first.get_body(preferencelist=("html",)).get_content()
    assert "On Mon" not in first.as_string()
    session = session.with_root(first["Message-ID"])
    second = build_message(
        _view(),
        session,
        user="devnull@example.com",
        to_addr="devnull@example.com",
    )
    assert second["Subject"] == first["Subject"]
    assert second["In-Reply-To"] == first["Message-ID"]
    assert first["Message-ID"] in (second["References"] or "")


def test_pause_keeps_token_and_blocks_send(tmp_path) -> None:
    save_smtp("devnull@example.com", "secret-pass", tmp_path)
    session = ensure_session("main", tmp_path)
    token = session.token
    mid = "<root@mail.optionda>"
    session = session.with_root(mid)
    session.save(tmp_path)
    pause_session(tmp_path, token)
    held = load_session(tmp_path)
    assert held is not None
    assert held.token == token
    assert held.message_id == mid
    assert held.paused is True
    sent: list[EmailMessage] = []

    def fake_smtp(msg, creds) -> None:
        sent.append(msg)

    try:
        send_desk(_view(), tmp_path, smtp_send=fake_smtp)
        raised = False
    except Exception:
        raised = True
    assert raised or sent == []
    resume_session(tmp_path, token)
    send_desk(_view(), tmp_path, smtp_send=fake_smtp)
    assert sent
    assert sent[0]["In-Reply-To"] == mid


def test_delete_mail_leaves_book_and_alpaca(tmp_path) -> None:
    from optionda.credentials import save_alpaca
    from optionda.journal import log_path
    from optionda.store import AccountStore

    store = AccountStore(tmp_path)
    store.create("main")
    store.activate("main")
    save_alpaca("key", "secret", tmp_path)
    log_path("main", tmp_path).write_text(
        '{"event":"add","occ":"AAPL261120C00350000"}\n',
        encoding="utf-8",
    )
    save_smtp("devnull@example.com", "secret-pass", tmp_path)
    ensure_session("main", tmp_path)
    clear_mail(tmp_path)
    assert load_smtp(tmp_path) is None
    assert load_session(tmp_path) is None
    assert store.exists("main")
    from optionda.credentials import load_alpaca

    alpaca = load_alpaca(tmp_path)
    assert alpaca is not None
    assert alpaca.key_id == "key"
    assert log_path("main", tmp_path).read_text(encoding="utf-8")


def test_delete_thread_mints_new_token(tmp_path) -> None:
    first = ensure_session("main", tmp_path)
    delete_thread(tmp_path)
    second = ensure_session("main", tmp_path)
    assert second.token != first.token
    assert second.subject != first.subject


def test_next_slot_wait_aligns_to_clock() -> None:
    from optionda.mailer import next_slot_wait

    noon = datetime(2026, 8, 24, 12, 0, 0)
    mid = datetime(2026, 8, 24, 12, 7, 0)
    half = datetime(2026, 8, 24, 12, 30, 0)
    late = datetime(2026, 8, 24, 23, 45, 0)
    assert next_slot_wait(30, noon) == 0
    assert next_slot_wait(30, half) == 0
    assert next_slot_wait(30, mid) == 23 * 60
    assert next_slot_wait(60, mid) == 53 * 60
    assert next_slot_wait(15, mid) == 8 * 60
    assert next_slot_wait(30, late) == 15 * 60
    assert next_slot_wait(30, datetime(2026, 8, 24, 12, 0, 1)) == 30 * 60 - 1
    assert next_slot_wait(30, noon, inclusive=False) == 30 * 60
    from optionda.mailer import next_slot_label

    assert next_slot_label(30, mid) == "12:30"
    assert next_slot_label(30, noon, inclusive=False) == "12:30"


def test_run_every_sends_on_aligned_slot(tmp_path) -> None:
    from optionda.mailer import run_every

    sent: list[int] = []
    sleeps: list[float] = []
    run_every(
        30,
        lambda: sent.append(1),
        home=tmp_path,
        sleep=sleeps.append,
        cycles=1,
        now=lambda: datetime(2026, 8, 24, 12, 0, 0),
    )
    assert sent == [1]
    assert sleeps == []


def test_run_every_waits_for_next_clock_slot(tmp_path) -> None:
    from optionda.mailer import run_every

    clock = {"t": datetime(2026, 8, 24, 12, 7, 0)}
    sent: list[int] = []
    sleeps: list[float] = []

    def now() -> datetime:
        return clock["t"]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] = clock["t"] + timedelta(seconds=seconds)

    run_every(
        30,
        lambda: sent.append(1),
        home=tmp_path,
        sleep=sleep,
        cycles=1,
        now=now,
    )
    assert sleeps == [23 * 60]
    assert sent == [1]


def test_run_every_skips_send_while_paused(tmp_path) -> None:
    from optionda.mailer import run_every

    ensure_session("main", tmp_path)
    pause_session(tmp_path)
    clock = {"t": datetime(2026, 8, 24, 12, 0, 30)}
    sent: list[int] = []
    sleeps: list[float] = []

    def now() -> datetime:
        return clock["t"]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] = clock["t"] + timedelta(seconds=seconds)

    run_every(
        1,
        lambda: sent.append(1),
        home=tmp_path,
        sleep=sleep,
        cycles=2,
        now=now,
    )
    assert sent == []
    assert sleeps == [30, 60]


def test_spawn_mail_every_is_detached(tmp_path, monkeypatch) -> None:
    from optionda.mailer import spawn_mail_every, worker_pid_path

    captured: dict = {}

    class FakeProc:
        pid = 4242

    def fake_popen(*args, **kwargs):
        captured["args"] = kwargs.get("args") or (args[0] if args else None)
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr("optionda.mailer.subprocess.Popen", fake_popen)
    proc = spawn_mail_every(30, tmp_path)
    assert proc.pid == 4242
    argv = captured["args"]
    assert "--every" in argv
    assert "30" in argv
    assert "--foreground" in argv
    assert worker_pid_path(tmp_path).read_text(encoding="utf-8").strip() == "4242"


def test_pack_omits_mail_secrets(tmp_path) -> None:
    from optionda.store import AccountStore
    from optionda.sync import pack_account, decode_code

    store = AccountStore(tmp_path)
    store.create("main")
    store.activate("main")
    save_smtp("devnull@example.com", "secret-pass", tmp_path)
    ensure_session("main", tmp_path)
    packed = pack_account(store, home=tmp_path)
    blob = packed.code
    assert "secret-pass" not in blob
    assert "devnull@example.com" not in blob
    bundle = decode_code(packed.code)
    raw = str(bundle)
    assert "secret-pass" not in raw
    assert "devnull@example.com" not in raw
