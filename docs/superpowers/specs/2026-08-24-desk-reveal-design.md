# Desk first-paint reveal (optionda)

## Goal

`run` / `export` in the GUI must never flash an empty page, then an empty table, then a full book. Show progress immediately, explain the current step in English, then reveal the main table one row at a time. After the first reveal, keep selective updates.

## Sequence

1. On submit: command echo + full-pane progress (spinner, `done/total`, bar, one English hint). Dashed frame is visible. No empty table, no `(no positions)`.
2. While fetch/mark runs: chrome-only updates. `DeskRunner` does not paint an empty snapshot.
3. When rows are ready: title + column headers, then `today +` rows, then `today −` rows, then footer (Σ / rPnL / tPnL). Section totals follow rows already shown.
4. After the last frame: current selective chrome / flash / poll updates. Later cycles do not replay the cascade.
5. CLI is unchanged.

## Loading copy

Map the engine label to one short English line. Optional tickers may follow.

| Signal | Hint |
|---|---|
| default / `updating` | `Getting the latest marks.` |
| clock / calendar | `Reading the market clock and session calendar.` |
| session / daily close | `Syncing the last completed US session and official closes.` |
| chain | `Calibrating IV surfaces from option chains.` |
| spots | `Fetching live underlying spots.` |
| mark | `Pricing each open position.` |
| writing | `Saving the book snapshot.` |

After the table is live, busy polls stay on the compact status bar.

## Ownership

- Immediate progress: `MainWindow._start_desk` (same pattern as add).
- Empty-book busy chrome: `DeskRunner._chrome_from` / `_poll` (`page=True`, no paint).
- Cascade: GUI timer + `render_snapshot(..., reveal=)`.
- Stop / fail / tab close: cancel the timer. Do not treat a half-drawn table as settled.

## Timing

About 40ms per frame. If many legs, speed up so the whole cascade stays near 1s.

## Out of scope

- CLI first-paint animation.
- Replaying the cascade on later `run` polls.
- Changing Model$ / close / IV rules.
