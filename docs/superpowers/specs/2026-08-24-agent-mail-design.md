# Agent pipe + local SMTP mail (optionda 1.2.0)

## Goal

Give agents a compact desk JSON, and mail the same desk to you over local SMTP. The GUI window stays the human desk. Mail is a separate headless process. No Grok, no Gmail API, no MCP send.

## Agent snapshot

`optionda snapshot` marks the active book (same path as `export`), writes `<home>/agent/latest.json`, and prints compact JSON. `--text` prints the monospaced table. `--cached` reprints the last file without a new mark.

Row fields: `occ`, `side`, `qty`, `spot`, `model_iv`, `cost`, `model`, `upnl`, `today`, `dte`, `last`, `section` (`+` / `−`). Top-level: `account`, `ts`, `feed`, `n`, `sum_model`, `sum_upnl`, `rpnl`, `tpnl`, `up[]`, `down[]`. No email identity.

Journal `event` is `snapshot`. Desk numbers only.

## SMTP mail

`optionda mail login <email> <app-password>` stores `[smtp]` next to Alpaca in `credentials.toml` (0600). Host default `smtp.gmail.com:587` STARTTLS. Same Gmail; app password after 2FA. Same optionda account as the window.

- `mail` — one shot into the current token thread. Refuses if paused unless `--force`.
- `mail --every 30` — detaches like the GUI window (prompt returns). Same thread, aligned to the wall clock (`:00` / `:30`). Wait for the next slot after start. `mail stop` kills the worker; `--foreground` keeps it in the terminal.
- `mail list` / `pause` / `resume` / `delete` (`--log` / `--login` / `--thread`). Pause keeps token, subject, and root Message-ID. `delete --thread` ends the conversation.

Session file: `<home>/mail/session.json`. Token is `secrets.token_hex(16)`, minted when the GUI window starts or mail starts. Subject is fixed: `optionda · {account} · {token[:8]}`.

MIME: `multipart/alternative` HTML desk (inline styles, `today +` / `today −`, run columns) plus a text table. No quoted prior mail, no images. Headers: `Auto-Submitted: auto-generated`, `List-Id`, `List-Unsubscribe`, `Precedence: bulk`, `X-Auto-Response-Suppress: All`. From display name `optionda`.

One-time Gmail filter (printed after login): subject starts with `optionda ·` → label `optionda`, Skip Inbox, Updates, never important.

`--every` may pass `--update` to check PyPI each cycle. After a successful upgrade, print a restart hint and exit.

Journal `event` is `mail`. Never From/To, app password, token, or Message-ID.

## Update

`optionda update` compares `__version__` to PyPI `optionda`. Newer → `pip install -U optionda==<latest>`. Current → `ok <version>`. Never deletes user data.

## Privacy

SMTP password, Gmail address, token, and send log never go in git, PyPI, `optionda pack` / `.oda`, or the journal. Unpack on another machine must `mail login` again. Alpaca pack behavior is unchanged.

## Out of scope

Gmail MCP send, Gmail API OAuth, Windows service, inbound reply execution, GUI send button.
