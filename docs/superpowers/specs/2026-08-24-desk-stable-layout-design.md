# Desk stable layout (optionda)

## Goal

After the first desk table is on screen, later `run` polls must not jump the page up and down. One or two legs moving between `today +` and `today −` must not rebuild the page. A larger burst of movers may grow a section.

## Chrome

1. First `run` / `export` still uses the full-pane English progress page.
2. Once the table is revealed, the live pane keeps a **one-line** status slot for the rest of the session.
3. Idle: countdown (`15s` … `1s`). Busy: spinner + bar + `done/total` on that same line.
4. Later cycles never use `page=True` progress. `set_live_html` must not hide the reserved slot.
5. `fetch_live` updates chrome only at the start of a refresh; it does not repaint the table until new marks are ready.

CLI is unchanged (no reserved status widget).

## Sections

GUI settled snapshots (`reserve_sections=True`, no in-progress reveal):

- Always draw both `today +` and `today −` when the book has any rows.
- Each section is at least **2** rows. Missing rows are blank (no `(no positions)`, no fake numbers).
- Totals count real rows only.
- A third (or later) mover grows that section.
- Reveal cascade is unchanged until the table settles.

Empty book stays a single `(no positions)` table.

## Out of scope

- Hysteresis (do not hold a near-zero row in the old section).
- Sticky max height that only grows.
- CLI table padding.
- Model$ / close / IV rules.
