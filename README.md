# optionda

Terminal options desk: manage small multi-account option books, freeze IV, and reprice on live underlying prints.

```bash
pip install optionda
# or from this repo:
pip install -e ./optionda
```

**MODEL marks only** — delayed/indicative data, not executable quotes.

## Quick start (no API key)

Requires Python 3.11+ (conda `base` on 3.9 will fail — use a fresh env).

```bash
# recommended: project venv
py -3.11 -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
python -m pip install -U pip setuptools wheel
pip install -e .

# or conda
# conda create -n optionda python=3.12 -y
# conda activate optionda
# python -m pip install -U pip setuptools wheel
# pip install -e .

optionda create demo
optionda activate demo        # remembers the active book (no shell config changes)

optionda add AAPL270115C00200000 --qty 2 --entry 5.20

# per-line qty + cost (semicolon batch):
optionda add "INTC 261016 140 C x10 @ 3.482; SKHY 261016 200 C x1 @ 9.5"

# easiest batch: bare add → paste lines → blank line to finish
optionda add
# INTC 261016 140 C x10 @ 3.482
# TSLA 261218 500 C x2 @ 5.75
# <empty line>

optionda export
optionda run
```

`optionda activate <name>` writes the active account into this environment’s data directory — **no `.bashrc` required**. Without an active account, `export` / `run` / `add` / `sell` / `delete` are blocked.

**Prompt prefix (optional):** does **not** edit `~/.bashrc`.

```bash
# venv
optionda prompt install --target venv
source .venv/Scripts/activate

# conda (writes $CONDA_PREFIX/etc/conda/activate.d/…)
conda activate myenv
optionda prompt install --target conda
conda deactivate && conda activate myenv

optionda activate demo           # next prompt → cyan [demo]
```

If both `(venv)` and `(base)` are active, use `--target` explicitly. Tab title is always updated by `activate` / `deactivate`. Remove with `optionda prompt uninstall`.

If an older install added a global shell hook, clean it with: `optionda init`.

**Cost is required on every `add`**: use `@ 5.20` on the line or `--entry 5.20`. Re-adding the same OCC+side merges qty and sets cost to the quantity-weighted average `(q1·c1 + q2·c2) / (q1+q2)`.

`add` **without** `--iv` pulls IV from Alpaca (if key configured) or Yahoo. Use `--iv` only as fallback.

In the table, **`Model$`** is the theoretical premium (per share) from an **American** CRR tree (US equity/ETF default; set `option_style = "european"` in config for closed-form BS). **`Cost`** is your avg entry, and **`uPnL$`** compares them (unrealized). To lock in cash PnL, close with `sell` and check `realized`.

```bash
optionda sell SPCX260918P00100000 x1 @ 8.50   # partial or full close
optionda realized                              # sum of sell events

# copy the .oda file to the other machine
optionda pack                                  # write ./<account>.oda
optionda unpack desk.oda --yes                 # overwrite book+journal, restore keys, refresh-iv
```

Long close: `(exit − avg_cost) × multiplier × qty`. Short cover: `(avg_cost − exit) × multiplier × qty`. `delete` still removes a row without recording exit premium.

UI uses **Rich** (`Panel`, `Rule`, `Table`, `Live` spinner). No `tqdm` / `popen` required for the desk view.

## Optional Alpaca key (15s refresh)

```bash
optionda key alpaca <KEY_ID> <SECRET>   # verifies against Alpaca before saving
optionda key status                     # re-checks live credentials
optionda run                            # refresh every 15s
optionda key clear alpaca
```

`key alpaca` probes `data.alpaca.markets` (SPY latest trade). Invalid keys are **not** saved.

### Where data lives (automatic)

No extra setup. Books / keys / logs follow the active environment:

| Situation | Data directory |
|-----------|----------------|
| `conda activate …` or a venv | `<env>/share/optionda` (isolated) |
| No virtual env | `~/.optionda` |
| `OPTIONDA_HOME=…` set | that path (manual override) |

```bash
optionda home    # show the path used right now
```

Credentials are `credentials.toml` inside that directory (mode `0600` when the OS allows).

Per-account tracking files (under the data library above, **not** your shell cwd):

Two separate write paths:

| Path | Role | Write mode |
|------|------|------------|
| `<data>/books/<account>.txt` | Current book only (human snapshot) | **Overwrite** on add/sell/delete/refresh |
| `<data>/logs/<account>.jsonl` | Full event stream for charts / history | **Append only** |
| `<data>/surfaces/<underlying>.json` | Last valid Alpaca IV smile | **Overwrite** only on successful `refresh-iv` |

JSONL `event` types: `add`, `merge`, `sell`, `delete`, `refresh_iv`, `export`, `run`.  
`sell` records exit premium and realized cash PnL. `refresh_iv` records calibrated surface metadata. `export`/`run` rows include `valuation_mode`, `surface_iv`, and `surface_as_of`.

```bash
optionda add …          # rewrite book + append add/merge event
optionda sell … @ …     # reduce/close qty + append sell (realized)
optionda delete …       # rewrite book + append delete event (no exit PnL)
optionda realized       # sum realized from sell events
optionda pack           # write ./<account>.oda (book+slim journal+keys)
optionda unpack FILE.oda  # replace account+journal, restore keys, auto refresh-iv
optionda refresh-iv     # freeze last-session Alpaca smiles (default ≤18h); --fresh for RTH
optionda export         # print surface/frozen Model$ + append export mark
optionda run            # each tick appends a run mark
```

**Spot (24/5):** Alpaca stock spots query `overnight` → `boats` → `delayed_sip` → `iex` and keep the **newest** trade/quote. Basic plans usually get `overnight` (≈Futu night session); `boats` needs a higher data tier.

### Local overnight IV surface

Run `optionda refresh-iv` anytime after the US close (default): it freezes the **last session** smile from Alpaca chain quotes up to **18 hours** old. Use `optionda refresh-iv --fresh` only when you want live RTH quotes (≤20 minutes).

Then `run` / `export` update the 24/5 stock Spot, evaluate both **sticky-strike** and **sticky-delta** scenarios on the saved smile, and reprice with the configured exercise style (American by default). The default Base Model is their 50/50 hybrid; its low/high scenario bounds and all inputs are recorded in JSONL. The terminal remains compact, while `optionda backtest` summarizes logged mark error and suggests a hybrid weight.

Surface calibration uses timestamped bid/ask mids to derive the market-standard European IV convention, then recomputes Delta with the same configured American model used for marks. Put and call wings are always kept separate. A Friday surface remains usable through the weekend; stale/missing-timestamp quotes are rejected.

This is a local, auditable model—not a copy of Futu's proprietary IV surface. Alpaca's free `indicative` chain is still the calibration input; OPRA improves the input only when the user has a subscription. optionda deliberately does **not** infer a new IV from frozen overnight option quotes.

**Visualization:** IV surfaces are not drawn in the terminal (ASCII heatmaps are too noisy for Live). Inspect in the browser: `pip install 'optionda[viz]'` then `optionda surface SPCX` (Plotly 3D).

```toml
# ~/.optionda/config.toml
alpaca_options_feed = "auto"       # try opra, then indicative
option_style = "american"          # US stock / ETF default
overnight_iv_mode = "hybrid"       # hybrid | sticky_delta | sticky_strike
sticky_delta_weight = 0.5
# Optional term rates and per-symbol continuous dividend yields:
rate_curve = [[30, 0.04], [90, 0.042]]
dividend_yields = { XOM = 0.035 }
```

For a paid match to exchange IV, subscribe to Alpaca OPRA.

## Commands

| Command | Purpose |
|---------|---------|
| `optionda create <name>` | Create account |
| `optionda list` | List accounts (`*` = active) |
| `optionda book` | Show current positions (no fetch / no log write) |
| `optionda activate <name>` | Set active account (persisted in data home) |
| `optionda deactivate` | Clear active account |
| `optionda home` | Show data directory for this environment |
| `optionda surface [TICKER]` | Open Plotly 3D IV surface in the browser (`optionda[viz]`) |
| `optionda init` | Remove leftover shell hook only (optional cleanup) |
| `optionda add …` | Add with required cost; same OCC+side merges qty + avg cost |
| `optionda delete <id\|OCC>` | Remove position |
| `optionda refresh-iv` | Calibrate local Alpaca IV smiles and refresh fallback IVs |
| `optionda run` | Live table until Ctrl+C |
| `optionda export` | One-shot snapshot |
| `optionda key …` | Configure Alpaca credentials |

## Repository

Standalone project: [github.com/ybenzou/optionda](https://github.com/ybenzou/optionda). Not part of the Next.js frontend app.
