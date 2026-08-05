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
# local install — prompt shows (optionda) like conda
py -3.11 -m venv --prompt optionda .venv
source .venv/Scripts/activate   # Git Bash → (optionda)
pip install -e .

optionda create demo
optionda add AAPL270115C00200000 --qty 2    # auto IV from Alpaca/Yahoo
optionda export
optionda run                                # spinner + red/green marks
```

`add` **without** `--iv` pulls IV from Alpaca (if key configured) or Yahoo. Use `--iv` only as fallback.

In the table, **`Model$`** is the Black–Scholes theoretical premium (per share). It is not a live option bid/ask.

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
| `optionda add …` | Add position (OCC or fields) |
| `optionda delete <id\|OCC>` | Remove position |
| `optionda refresh-iv` | Re-freeze IV from market |
| `optionda run` | Live table until Ctrl+C |
| `optionda export` | One-shot snapshot |
| `optionda key …` | Configure Alpaca credentials |

## Repository

Standalone project: [github.com/ybenzou/optionda](https://github.com/ybenzou/optionda). Not part of the Next.js frontend app.
