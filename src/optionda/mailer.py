from __future__ import annotations

import json
import os
import secrets
import smtplib
import subprocess
import sys
import time
from importlib import resources
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Callable

from optionda.agent_view import format_agent_text, render_desk_html
from optionda.credentials import SmtpCredentials, clear_smtp, load_smtp, save_smtp
from optionda.paths import ensure_home

MAIL_FILTER_HINT = (
    "Gmail cannot be labeled over SMTP. Import the filter once: "
    "optionda mail filter  →  Settings → Filters → Import filters. "
    "Subject starts with 'optionda ·' → label optionda, Skip Inbox, Updates."
)

GMAIL_FILTER_IMPORT = (
    "https://mail.google.com/mail/u/0/#settings/filters"
)


class MailError(Exception):
    pass


def _mail_dir(home: Path | None) -> Path:
    root = ensure_home(home)
    path = root / "mail"
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_path(home: Path | None = None) -> Path:
    return _mail_dir(home) / "session.json"


def sends_path(home: Path | None = None) -> Path:
    return _mail_dir(home) / "sends.jsonl"


@dataclass
class MailSession:
    token: str
    subject: str
    message_id: str | None = None
    started_at: str | None = None
    paused: bool = False
    n: int = 0

    def with_root(self, message_id: str | None) -> MailSession:
        return replace(self, message_id=message_id)

    def save(self, home: Path | None = None) -> Path:
        path = session_path(home)
        path.write_text(
            json.dumps(
                {
                    "token": self.token,
                    "subject": self.subject,
                    "message_id": self.message_id,
                    "started_at": self.started_at,
                    "paused": self.paused,
                    "n": self.n,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return path


def load_session(home: Path | None = None) -> MailSession | None:
    path = session_path(home)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    token = str(raw.get("token") or "").strip()
    if not token:
        return None
    return MailSession(
        token=token,
        subject=str(raw.get("subject") or ""),
        message_id=raw.get("message_id"),
        started_at=raw.get("started_at"),
        paused=bool(raw.get("paused")),
        n=int(raw.get("n") or 0),
    )


def _subject_for(account: str, token: str) -> str:
    return f"optionda · {account} · {token[:8]}"


def ensure_session(account: str, home: Path | None = None) -> MailSession:
    current = load_session(home)
    if current is not None:
        if not current.subject:
            current.subject = _subject_for(account, current.token)
            current.save(home)
        return current
    token = secrets.token_hex(16)
    session = MailSession(
        token=token,
        subject=_subject_for(account, token),
        started_at=datetime.now(timezone.utc).isoformat(),
        paused=False,
        n=0,
    )
    session.save(home)
    return session


def pause_session(home: Path | None = None, token: str | None = None) -> MailSession:
    session = load_session(home)
    if session is None:
        raise MailError("no mail session")
    if token and not session.token.startswith(token) and session.token != token:
        raise MailError("token mismatch")
    session.paused = True
    session.save(home)
    return session


def resume_session(home: Path | None = None, token: str | None = None) -> MailSession:
    session = load_session(home)
    if session is None:
        raise MailError("no mail session")
    if token and not session.token.startswith(token) and session.token != token:
        raise MailError("token mismatch")
    session.paused = False
    session.save(home)
    return session


def delete_thread(home: Path | None = None) -> bool:
    path = session_path(home)
    if not path.exists():
        return False
    path.unlink()
    return True


def clear_sends(home: Path | None = None) -> bool:
    path = sends_path(home)
    if not path.exists():
        return False
    path.unlink()
    return True


def clear_mail(home: Path | None = None) -> None:
    clear_smtp(home)
    clear_sends(home)
    delete_thread(home)


def _append_send(home: Path | None, record: dict) -> None:
    safe = {
        "ts": record.get("ts"),
        "subject": record.get("subject"),
        "ok": record.get("ok"),
        "n": record.get("n"),
    }
    path = sends_path(home)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(safe, ensure_ascii=False) + "\n")


def read_sends(home: Path | None = None, *, limit: int = 8) -> list[dict]:
    path = sends_path(home)
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def gmail_filter_xml() -> str:
    path = resources.files("optionda").joinpath("gmail-filter.xml")
    return path.read_text(encoding="utf-8")


def write_gmail_filter(home: Path | None = None) -> Path:
    path = _mail_dir(home) / "gmail-filter.xml"
    path.write_text(gmail_filter_xml(), encoding="utf-8")
    return path


def _short_ts(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return "—"
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:16]
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    local = when.astimezone()
    return f"{local.month}/{local.day} {local.hour:02d}:{local.minute:02d}"


def format_list(home: Path | None = None) -> str:
    creds = load_smtp(home)
    session = load_session(home)
    worker = live_worker_pid(home)
    if session is None:
        state = "off" if creds is None else "idle"
        token8 = "—"
        subject = "—"
        sends = 0
    elif session.paused:
        state = "paused"
        token8 = session.token[:8]
        subject = session.subject or f"optionda · {token8}"
        sends = session.n
    else:
        state = "running" if worker is not None else "idle"
        token8 = session.token[:8]
        subject = session.subject or f"optionda · {token8}"
        sends = session.n
    lines = [
        f"mail  {state}  {token8}",
        f"  smtp    {creds.user if creds else '—'}",
        f"  thread  {subject}",
        f"  sends   {sends}    worker {worker if worker is not None else '—'}",
    ]
    for item in read_sends(home):
        mark = "ok" if item.get("ok") else "fail"
        lines.append(f"  {_short_ts(item.get('ts'))}  {mark}")
    return "\n".join(lines)


def build_message(
    view: dict,
    session: MailSession,
    *,
    user: str,
    to_addr: str,
) -> EmailMessage:
    msg = EmailMessage()
    seq = session.n + 1
    msg["Message-ID"] = f"<{session.token}.{seq}@mail.optionda>"
    msg["Subject"] = session.subject
    msg["From"] = formataddr(("optionda", user))
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    msg["Auto-Submitted"] = "auto-generated"
    msg["List-Id"] = "optionda desk <desk.optionda>"
    msg["List-Unsubscribe"] = "<mailto:devnull@example.com>"
    msg["Precedence"] = "bulk"
    msg["X-Auto-Response-Suppress"] = "All"
    if session.message_id:
        msg["In-Reply-To"] = session.message_id
        msg["References"] = session.message_id
    html = render_desk_html(view)
    text = format_agent_text(view)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def smtp_send(msg: EmailMessage, creds: SmtpCredentials) -> None:
    with smtplib.SMTP(creds.host, creds.port, timeout=30) as client:
        client.starttls()
        client.login(creds.user, creds.password)
        client.send_message(msg)


def send_desk(
    view: dict,
    home: Path | None = None,
    *,
    to_addr: str | None = None,
    force: bool = False,
    smtp_send: Callable[[EmailMessage, SmtpCredentials], None] | None = None,
) -> EmailMessage:
    creds = load_smtp(home)
    if creds is None:
        raise MailError("mail is not configured — optionda mail login")
    account = str(view.get("account") or "optionda")
    session = ensure_session(account, home)
    if session.paused and not force:
        raise MailError("mail is paused — optionda mail resume")
    dest = (to_addr or creds.user).strip()
    msg = build_message(view, session, user=creds.user, to_addr=dest)
    deliver = smtp_send if smtp_send is not None else sys.modules[__name__].smtp_send
    deliver(msg, creds)
    if session.message_id is None:
        session.message_id = msg["Message-ID"]
    session.n += 1
    session.save(home)
    _append_send(
        home,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "subject": session.subject,
            "ok": True,
            "n": session.n,
        },
    )
    return msg


def next_slot_wait(
    minutes: int,
    now: datetime | None = None,
    *,
    inclusive: bool = True,
) -> float:
    """Seconds until the next wall-clock multiple of ``minutes`` (from midnight)."""
    when = now or datetime.now()
    interval = max(int(minutes), 1) * 60
    midnight = when.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (when - midnight).total_seconds()
    into = elapsed % interval
    if into == 0:
        return 0.0 if inclusive else float(interval)
    return float(interval - into)


def next_slot_at(
    minutes: int,
    now: datetime | None = None,
    *,
    inclusive: bool = True,
) -> datetime:
    when = now or datetime.now()
    return when + timedelta(seconds=next_slot_wait(minutes, when, inclusive=inclusive))


def next_slot_label(
    minutes: int,
    now: datetime | None = None,
    *,
    inclusive: bool = True,
) -> str:
    return next_slot_at(minutes, now, inclusive=inclusive).strftime("%H:%M")


def worker_pid_path(home: Path | None = None) -> Path:
    return _mail_dir(home) / "worker.pid"


def worker_log_path(home: Path | None = None) -> Path:
    return _mail_dir(home) / "worker.log"


def write_worker_pid(pid: int, home: Path | None = None) -> Path:
    path = worker_pid_path(home)
    path.write_text(f"{int(pid)}\n", encoding="utf-8")
    return path


def spawn_mail_every(
    minutes: int,
    home: Path | None = None,
    *,
    extra: list[str] | None = None,
) -> subprocess.Popen:
    root = ensure_home(home)
    log = worker_log_path(root)
    command = [
        sys.executable,
        "-m",
        "optionda",
        "mail",
        "--every",
        str(max(int(minutes), 1)),
        "--foreground",
    ]
    if extra:
        command.extend(extra)
    env = os.environ.copy()
    env["OPTIONDA_HOME"] = str(root)
    handle = log.open("a", encoding="utf-8")
    kwargs: dict = {
        "args": command,
        "stdin": subprocess.DEVNULL,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "env": env,
        "close_fds": True,
    }
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(**kwargs)
    finally:
        handle.close()
    write_worker_pid(proc.pid, root)
    return proc


def read_worker_pid(home: Path | None = None) -> int | None:
    path = worker_pid_path(home)
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        return None
    return int(raw)


def live_worker_pid(home: Path | None = None) -> int | None:
    pid = read_worker_pid(home)
    if pid is None:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def stop_mail_worker(home: Path | None = None) -> int | None:
    pid = read_worker_pid(home)
    path = worker_pid_path(home)
    if path.exists():
        path.unlink()
    if pid is None:
        return None
    if pid != os.getpid():
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    return pid


def run_every(
    minutes: int,
    send_once: Callable[[], None],
    *,
    home: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
    cycles: int | None = None,
    now: Callable[[], datetime] | None = None,
) -> None:
    clock = now or datetime.now
    inclusive = True
    done = 0
    while True:
        if should_stop is not None and should_stop():
            return
        wait = next_slot_wait(minutes, clock(), inclusive=inclusive)
        inclusive = False
        if wait > 0:
            sleep(wait)
        if should_stop is not None and should_stop():
            return
        session = load_session(home)
        if session is None or not session.paused:
            send_once()
        done += 1
        if cycles is not None and done >= cycles:
            return
