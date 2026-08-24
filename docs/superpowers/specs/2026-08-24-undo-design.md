# Undo last command (optionda)

## Goal

Reverse the last mutating command as one unit. Keep an append-only `undo` event so the journal shows it happened and was undone. Restore open qty/cost and net realized cash PnL.

## Command

```text
optionda undo
```

Same token works in the GUI shell. Prints which legs were reversed and the realized delta.

## Grouping

- Each `add` batch (including embedded `sell` lines) and each standalone `sell` writes a shared `batch_id` on every journal event.
- `undo` reverses the last mutation group: same `batch_id`, or — for older logs — consecutive `add`/`merge`/`sell`/`delete` within 30 seconds.
- A trailing `undo` is its own group. Undoing it restores the previous book (redo).
- `export` / `run` / `refresh_iv` are skipped when finding the last mutation.

## Book and realized

- Restore `account.positions` from the `book` snapshot on the event immediately before the group.
- `undo.realized` is the negation of sell realized in that group.
- `realized_pnl_summary`, daily marks, and stats totals include `undo.realized`.
- Journal is never rewritten.

## Out of scope

- Editing or deleting older real fills.
- Changing IV surfaces.
- Partial undo of one leg inside a batch.
