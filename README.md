# optionda

Terminal options desk: manage small multi-account option books, freeze IV, and reprice on live underlying prints.

```bash
pip install optionda
# or from this repo:
pip install -e ./optionda
```

**MODEL marks only** — delayed/indicative data, not executable quotes.

## Quick start (no API key)

Requires Python 3.11+.

```bash
py -3.11 -m venv .venv
source .venv/Scripts/activate
pip install -e .

# one-time (like conda init)
optionda init
eval "$(optionda shellenv)"   # or open a new shell — default prompt: [optionda] (cyan)

optionda create demo
optionda activate demo        # prompt → [demo]
optionda deactivate           # prompt → [optionda]
optionda activate hedge       # prompt → [hedge]

optionda add AAPL270115C00200000 --qty 2

# easiest batch: bare add → paste lines → blank line to finish
optionda add
# INTC 261016 140 C
# TSLA 261218 500 C
# <empty line>

# or one line with semicolons:
optionda add "INTC 261016 140 C; TSLA 261218 500 C"

optionda export
optionda run
```

`pip install` cannot safely edit your shell config (unlike the Conda installer). Run **`optionda init` once**, then **`optionda activate <name>`** each session (like `conda activate`). Undo init with `optionda init --reverse`.

Without `activate`, `export` / `run` / `add` / `delete` cannot read or change any account book — only the session-active account is visible.

`add` **without** `--iv` pulls IV from Alpaca (if key configured) or Yahoo. Use `--iv` only as fallback.

In the table, **`Model$`** is the Black–Scholes theoretical premium (per share). It is not a live option bid/ask.

UI uses **Rich** (`Panel`, `Rule`, `Table`, `Live` spinner). No `tqdm` / `popen` required for the desk view.

## Optional Alpaca key (15s refresh)

```bash
optionda key alpaca <KEY_ID> <SECRET>   # verifies against Alpaca before saving
optionda key status                     # re-checks live credentials
optionda run                            # refresh every 15s
optionda key clear alpaca
```

`key alpaca` probes `data.alpaca.markets` (SPY latest trade). Invalid keys are **not** saved.

Credentials live in `~/.optionda/credentials.toml` (mode `0600` when the OS allows). Override the data root with `OPTIONDA_HOME`.

Per-account tracking files (under the optionda data library, **not** your shell cwd):

| Path | Role |
|------|------|
| `~/.optionda/books/<account>.txt` | Human book; created/updated on `add` / `delete` |
| `~/.optionda/logs/<account>.log` | Append-only snapshots from `export` and each `run` refresh |

```bash
optionda add …          # refreshes books/demo.txt
optionda export         # prints table + appends logs/demo.log
optionda run            # every refresh appends a `run` block to the same log
```

## Commands

| Command | Purpose |
|---------|---------|
| `optionda create <name>` | Create account |
| `optionda list` | List accounts (`*` = session-active) |
| `optionda activate <name>` | Session-activate (prompt → cyan `[name]`) |
| `optionda deactivate` | Back to cyan `[optionda]` |
| `optionda init` | Persist hook in shell rc (like `conda init`) |
| `optionda add …` | Add one or many (file/`-`/multi-OCC; progress when batch) |
| `optionda delete <id\|OCC>` | Remove position |
| `optionda refresh-iv` | Re-freeze IV from market |
| `optionda run` | Live table until Ctrl+C |
| `optionda export` | One-shot snapshot |
| `optionda key …` | Configure Alpaca credentials |

## Repository

Standalone project: [github.com/ybenzou/optionda](https://github.com/ybenzou/optionda). Not part of the Next.js frontend app.
