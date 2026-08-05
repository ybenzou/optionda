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

# once per shell (conda-style): prompt shows (account)
eval "$(optionda shellenv)"

optionda create demo          # prompt → (demo)
optionda use hedge            # prompt → (hedge)
optionda add AAPL270115C00200000 --qty 2
optionda export
optionda run
```

Put `eval "$(optionda shellenv)"` in `~/.bashrc` if you want it every time.

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

## Commands

| Command | Purpose |
|---------|---------|
| `optionda create <name>` | Create account |
| `optionda list` / `use <name>` | List / select account |
| `optionda current` | Print current account (for hooks) |
| `optionda shellenv` | Print bash/zsh hook → `eval "$(optionda shellenv)"` |
| `optionda add …` | Add position (OCC or fields) |
| `optionda delete <id\|OCC>` | Remove position |
| `optionda refresh-iv` | Re-freeze IV from market |
| `optionda run` | Live table until Ctrl+C |
| `optionda export` | One-shot snapshot |
| `optionda key …` | Configure Alpaca credentials |

## Repository

Standalone project: [github.com/ybenzou/optionda](https://github.com/ybenzou/optionda). Not part of the Next.js frontend app.
