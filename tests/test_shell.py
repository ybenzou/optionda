from optionda.display.table import _inline_bar, render_snapshot
from optionda.gui.richview import renderable_html
from optionda.gui.shell import dispatch, parse_line


def test_inline_bar_is_ascii() -> None:
    empty = _inline_bar(0.0, width=12, busy=True)
    assert empty.plain == "-" * 12
    mid = _inline_bar(0.5, width=12, busy=True)
    assert "#" in mid.plain
    assert "-" in mid.plain
    assert all(ch in "#-" for ch in mid.plain)


def test_renderable_html_keeps_desk_chrome() -> None:
    html = renderable_html(
        render_snapshot(
            account="main",
            feed="alpaca",
            refresh_sec=15,
            rows=[],
            realized=1262.0,
            continuous=True,
            poll_busy=True,
            poll_label="1/2 fetch",
            poll_done=0,
            poll_total=1,
        ),
        140,
    )
    assert "main" in html
    assert "optionda" in html
    assert "alpaca" in html
    assert "<pre" in html
    assert html.count("\n") >= 3
    assert "#" in html


def test_parse_line_strips_optionda_prefix() -> None:
    assert parse_line("list") == ["list"]
    assert parse_line("optionda activate main") == ["activate", "main"]
    assert parse_line('add "AAPL 261120 350 C x1 @ 3.4"') == [
        "add",
        "AAPL 261120 350 C x1 @ 3.4",
    ]
    assert parse_line("") == []


def test_dispatch_builtins() -> None:
    assert dispatch("exit").action == "exit"
    assert dispatch("clear").action == "clear"
    assert dispatch("term").action == "term"
    stats = dispatch("stats 1m")
    assert stats.action == "stats"
    assert stats.period == "all"
    help_text = dispatch("help")
    assert "activate" in help_text.text
    assert "pack" in help_text.text
    assert "unpack" in help_text.text
    assert "refresh-iv" in help_text.text
    assert help_text.action == "none"
    assert dispatch("run").action == "run"
    assert dispatch("export").action == "export"
    assert dispatch("stop").action == "stop"


def test_dispatch_create_and_list(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.delenv("OPTIONDA_ACTIVE", raising=False)
    created = dispatch("create main", home=tmp_path)
    assert created.code == 0, created.text
    activated = dispatch("activate main", home=tmp_path)
    assert activated.code == 0, activated.text
    shown = dispatch("list", home=tmp_path)
    assert shown.code == 0, shown.text
    assert "main" in shown.text


def test_dispatch_pack_uses_cli(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert dispatch("create demo", home=tmp_path).code == 0
    assert dispatch("activate demo", home=tmp_path).code == 0
    packed = dispatch("pack", home=tmp_path)
    assert packed.code == 0, packed.text
    assert "oda1." in packed.text or "packed" in packed.text.lower()


def test_desk_runner_export_paints_progress(tmp_path, monkeypatch) -> None:
    from unittest.mock import patch

    from optionda.desk_live import DeskRunner
    from optionda.market.session import SessionSyncResult
    from optionda.store import AccountStore

    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store = AccountStore(tmp_path)
    store.create("demo")
    store.activate("demo")
    frames: list[object] = []
    with (
        patch("optionda.desk_live.mark_account", return_value=[]) as mark,
        patch(
            "optionda.desk_live.sync_completed_session",
            return_value=SessionSyncResult(),
        ),
    ):
        DeskRunner(
            home=tmp_path,
            store=store,
            paint=frames.append,
        ).run_once(source="export")
    assert mark.called
    assert frames
    assert any(getattr(frame, "renderables", None) or frame for frame in frames)
