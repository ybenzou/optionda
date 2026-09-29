# optionda

本地期权记账与盯市，当前版本 1.5.0。管的是你自己的小账本：冻结隐含波动率，再用标的的最新成交价把持仓重估成模型价。它不是券商下单终端，桌面上的价格也不能拿去成交。

下面描述的是这个仓库里的当前程序。`pip install optionda` 装到的是已经发布的版本；要跑仓库里的这一版，在 `optionda` 环境里对这份源码做可编辑安装。

## 项目说明

### 它在算什么

每一行持仓有两个你要分开看的数。

**Cost** 是你的平均建仓价，按股计。同一张合约、同一方向再次加仓时，成本按数量加权：`(q1·c1 + q2·c2) / (q1+q2)`。没有成本就不能 `add`。

**Model$** 是理论权利金，也是按股计。波动率在 `refresh-iv` 时从期权链上冻住，之后不再用隔夜期权报价去反推一个新的波动率。盯市时变的是标的现货。默认用美式 CRR 树；配置里把 `option_style` 改成 `european` 才走闭式解。`uPnL$` 是模型价相对成本的浮动盈亏，多头为 `(模型 − 成本) × 乘数 × 数量`。

已经落袋的盈亏只在 `sell` 时入账。平多是 `(卖出价 − 平均成本) × 乘数 × 数量`，平空是 `(平均成本 − 买回价) × 乘数 × 数量`。`delete` 只是把一行拿掉，不记出场价，所以不算已实现盈亏。`optionda realized` 把账本里的卖出和对应撤销加总。

现货用最新成交，不用买卖报价的中间价。有 Alpaca 时，股票成交按 `overnight`、`boats`、`delayed_sip`、`iex` 的顺序看，留下时间最新的那一笔。官方收盘是 Alpaca 的日线，用来做日终模型和策略图上的历史点。期权的实时中间价只在 `verify` 里出现，用来对照模型，不会写成桌面上的 Model$。

### 账本和行情是两本账

成交顺序在账本里。`ledger/<账户>.sqlite` 的 `events` 表用整数 `seq` 当主键，这就是记账顺序，不按时间戳重排。`add`、`merge`、`sell`、`delete`、`undo`、`refresh_iv` 写在这里。撤销是再追加一条 `undo`，不改已经写下的行。

行情快照在另一本库里。`quotes/<账户>.sqlite` 每次轮询为每张合约追加一行，`latest` 表才是桌面立刻要读的最新一行。主库只留近一个季度；更早的快照原样搬进 `quotes/<账户>.archive.sqlite`，不删除。回测先读归档，再读主库。快照里可以区分曲面交易日、曲面时间、模型上下带、现货时间、当时用的利率和分红。旧行这些字段是空的，不会回填一个编出来的数。

`books/<账户>.txt` 只是给人看的当前持仓，每次加减仓都会整份重写。`logs/<账户>.jsonl` 是更早的事件文件；交易记录迁进 sqlite 之后，它不再是图表和已实现盈亏的来源。邮件密码、令牌、Message-ID 不会进账本，也不会进 `pack` 打出来的 `.oda`。

### 窗口里有什么

`optionda` 打开的是 PySide6 窗口，不是浏览器页面。`optionda run` 在同一扇窗口里盯市：左边是持仓表，右边是策略图。有 Alpaca 钥匙时大约 15 秒刷新一次，否则走 Yahoo，间隔更长。曲面校准放在子进程里，避免整段时间把窗口卡住。

策略图默认是包含上一周的两个星期。可以改成月或年。横轴是交易日，周五到周一的空隙被压窄，图不能自由缩放，否则这个比例会被拖乱。年视图从这一年最早有记录的月份画到十二月；一月就有记录时才从一月开始。折线是每个交易日的模型价相对成本的百分比。15 秒一次的快照不会接到这条日线后面。

点一条线或左下图例上的名字就聚焦这张合约，再点一次取消。聚焦之后，加仓是向上的实心箭头，减仓是向下的实心箭头，建仓的第一天也算一次加仓。箭头不横跨两条线。两条线离得近或交叉时，箭头缩成贴在盈亏线外的小三角。这一段如果上涨，面积顶和盈亏线之间填绿；下跌填红。左边的名字和图例保持这张合约自己的颜色。同一标的、同一行权价有两张合约时，左边的短名字才加上月和日，避免把窄表撑开。

已经平掉的合约，只要出场日落在当前周、月或年里，就留在图上。数量回到 0，百分比不在出场那天被拉回 0。更早平掉、这个窗口里没有点的，不出现。图例在左边持仓表下面，按当前张数从大到小，列出颜色、持仓天数和已实现。十字线只列出真正穿过那一天的合约，不放新闻标题。鼠标离开图，十字线马上消失。

周视图里，只有「今天、而且当前聚焦的那一张」可以再画一条更淡的 live 线，图例写明 live。它用的是今天的模型快照，停在今天，不改昨天的收盘点。

`optionda stats` 是绩效页，周期可以是 `1m`、`3m`、`6m` 或 `all`。日历上点某一天，绩效图的竖线停在那天，KPI 旁边注明当日已实现，以及用当天收盘标记算的当日浮动，不用 15 秒快照。`optionda sql` 在主窗口里打开只读数据库浏览器。`optionda desk` 直接进入统计页。

策略图获得焦点时，`↑` `↓` 在当前窗口的合约之间移动，`[` `]` 是上一窗和下一窗。状态栏在还有可撤销事件时显示一行；命令框是空的时候按回车，执行和 `optionda undo` 相同的撤销。卖出仍然走命令，窗口里没有第二套下单表单。

### 波动率曲面

`optionda refresh-iv` 用 Alpaca 期权链上带时间戳的买卖中间价，算出市场上常用的欧式隐含波动率，再按你配置的行权方式重算 delta。认沽和认购的两翼分开保存。默认接受最近一个交易时段、最多约 18 小时内的报价；`--fresh` 才要求常规交易时段里更近的报价。成功的曲面写到 `surfaces/<标的>.json`，同一次校准再记一行 `surfaces/log.jsonl`（标的、交易日、接受和拒绝的节点数、耗时、错误）。失败不会覆盖上一份还能用的曲面。

周五冻住的曲面可以过周末继续用。`run` 和 `export` 在这张曲面上同时看粘性行权价和粘性 delta，默认模型是二者各半。上下带和当时的利率、分红会进行情快照。这是本地可以复查的模型，不是富途那套曲面的复制。免费的 `indicative` 链就够校准；有 OPRA 订阅时，输入更好，算法不变。

休市日来自已经拉到的 Alpaca 日历，记在 `closes/calendar.json`。策略图的点和横轴会跳过这些日子，而不是画成一条平的持仓日。

持仓新闻按配置里的 `news_poll_sec` 采集，写到 `news/items.jsonl`，不进账本，不进行情表，也不进十字线浮窗。`optionda news` 把最近约 24 小时的标题打到终端。

### 它故意不做什么

不把行情库并进账本。不把期权中间价显示成 Model$。不在图上拖拽改账，也不提供另一条卖出路径。成本线的样式维持现状。全链上每个行权价的美式 delta 也维持现状；如果以后只对持仓附近的行权价算美式 delta，那会改变曲面和以后的模型价，需要单独比较，不能当成一次性能修补。

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

桌面窗口用 PySide6。终端里的一次性表格仍用 Rich。盯市不依赖 `tqdm`。

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
| `<data>/ledger/<account>.sqlite` | Trade order (`events.seq`). Charts and realized PnL read this | **Append** events; never reorder by timestamp |
| `<data>/quotes/<account>.sqlite` | Poll snapshots. `latest` is the desk; `quotes` is history | **Append** history, **replace** `latest` |
| `<data>/quotes/<account>.archive.sqlite` | Snapshots older than about a quarter | **Move**, not delete |
| `<data>/books/<account>.txt` | Current book only (human snapshot) | **Overwrite** on add/sell/delete/refresh |
| `<data>/surfaces/<underlying>.json` | Last valid Alpaca IV smile | **Overwrite** only on successful `refresh-iv` |
| `<data>/surfaces/log.jsonl` | One calibration record per attempt | **Append** |
| `<data>/closes/calendar.json` | Sessions already fetched from Alpaca | **Merge** |
| `<data>/news/items.jsonl` | Headlines. Not part of the book | **Append** |
| `<data>/logs/<account>.jsonl` | Legacy event file. Not the book of record after the sqlite ledger exists | Left in place |

Ledger events include `add`, `merge`, `sell`, `delete`, `undo`, and `refresh_iv`. Quote rows are the old `export` / `run` / `snapshot` / `mail` marks. Neither store keeps Gmail, SMTP passwords, tokens, or Message-IDs.

```bash
optionda add …          # rewrite book + append add/merge event
optionda sell … @ …     # reduce/close qty + append sell (realized)
optionda delete …       # rewrite book + append delete event (no exit PnL)
optionda realized       # sum realized from sell events
optionda pack           # write ./<account>.oda (book+slim journal+keys)
optionda unpack FILE.oda  # replace account+journal, restore keys, auto refresh-iv
optionda refresh-iv     # freeze last-session Alpaca smiles (default ≤18h); --fresh for RTH
optionda export         # print surface/frozen Model$ and store a quote snapshot
optionda run            # open the desk: live marks, quote snapshots, strategy chart
```

**Spot (24/5):** Alpaca stock spots query `overnight` → `boats` → `delayed_sip` → `iex` and keep the **newest** trade/quote. Basic plans usually get `overnight` (≈Futu night session); `boats` needs a higher data tier.

### Local overnight IV surface

Run `optionda refresh-iv` anytime after the US close (default): it freezes the **last session** smile from Alpaca chain quotes up to **18 hours** old. Use `optionda refresh-iv --fresh` only when you want live RTH quotes (≤20 minutes).

Then `run` / `export` update the 24/5 stock Spot, evaluate both **sticky-strike** and **sticky-delta** scenarios on the saved smile, and reprice with the configured exercise style (American by default). The default Base Model is their 50/50 hybrid. Scenario bounds, the surface session, spot time, rate, and dividend are stored on new quote rows. `optionda backtest` reads both the archive and the main quote database, and can suggest a hybrid weight.

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
| `optionda run` | Live desk in the window (marks, quotes, strategy chart) |
| `optionda stats [1m\|3m\|6m\|all]` | Performance, calendar, and behavior |
| `optionda sql` | Read-only database browser in the main window |
| `optionda undo` | Append an undo of the last ledger batch |
| `optionda news` | Print holdings headlines from about the last 24 hours |
| `optionda export` | One-shot snapshot |
| `optionda snapshot` | Compact JSON for agents (`--text`, `--cached`) |
| `optionda mail …` | Local SMTP desk mail (`login` / `--every 30`) |
| `optionda update` | Compare this install to PyPI and upgrade |
| `optionda key …` | Configure Alpaca credentials |

## Headless mail (local SMTP)

The GUI window is the human desk. Mail is a **separate process** on the same machine (same `OPTIONDA_HOME` / active account). No Grok bot required.

```bash
# once: Gmail + 2FA app password (same inbox you already use)
optionda mail login you@gmail.com <app-password>

# one shot, or a detached loop on the clock (:00 / :30)
optionda mail
optionda mail --every 30
# prints: mail every 30 started  next 17:00  pid …
# prompt returns; worker keeps sending. Stop with:
optionda mail stop
```

One **session token** is minted when the window opens or mail starts. Subject stays `optionda · {account} · {token[:8]}`. Later sends are header replies (`In-Reply-To` / `References`) with a full run-style HTML desk — never a quoted plaintext thread.

```bash
optionda mail list              # login (no password), token, paused, recent sends
optionda mail pause             # keep token/thread; --every sleeps without SMTP
optionda mail resume
optionda mail delete            # login + send log + thread (never the book)
optionda mail delete --thread   # end this conversation only
```

**Stay out of Primary.** SMTP cannot stamp a Gmail label. Import the filter once:

```bash
optionda mail filter
```

Then Gmail → Settings → Filters and Blocked Addresses → Import filters → `gmail-filter.xml`, and tick **Apply new filters to existing conversations**. That applies label `optionda`, Skip Inbox, Updates, never important. Subject match: starts with `optionda ·`.

Mail secrets stay on this machine: SMTP password, Gmail address, token, and `mail/sends.jsonl` are **never** in git, PyPI, `optionda pack` / `.oda`, or the journal. Unpack on another PC and run `mail login` again. Alpaca key pack behavior is unchanged.

## Repository

Standalone project: [github.com/ybenzou/optionda](https://github.com/ybenzou/optionda). Not part of the Next.js frontend app.
