from datetime import date, datetime, timezone

from optionda.display.table import (
    DeskReveal,
    explain_progress,
    format_load_progress,
    render_snapshot,
    reveal_interval_ms,
    reveal_steps,
)
from optionda.models import Position, RowMark


def _row(occ: str, notional: float, *, up: bool) -> RowMark:
    close = 10.0
    theo = 12.0 if up else 8.0
    chg = theo - close
    return RowMark(
        position=Position(
            occ_symbol=occ,
            underlying=occ[:4],
            expiry=date(2026, 12, 18),
            strike=100.0,
            option_type="call",
            qty=1,
            side="long",
            iv_frozen=0.25,
            iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
            entry_premium=3.5,
        ),
        spot=100.0,
        theo=theo,
        delta=0.2,
        dte=90.0,
        notional=notional,
        cost=3.5,
        upnl=None,
        close_premium=close,
        theo_chg=chg,
    )


def test_explain_progress_maps_engine_labels() -> None:
    assert explain_progress(None) == "Getting the latest marks."
    assert explain_progress("updating…") == "Getting the latest marks."
    assert (
        explain_progress("1/2 fetch  clock / calendar…")
        == "Reading the market clock and session calendar."
    )
    assert (
        explain_progress("1/2 fetch  daily close AAPL HOOD")
        == "Syncing the last completed US session and official closes.  AAPL HOOD"
    )
    assert (
        explain_progress("2/2 chain  AVGO chain…")
        == "Calibrating IV surfaces from option chains.  AVGO"
    )
    assert (
        explain_progress("1/2 fetch  spots · AAPL HOOD")
        == "Fetching live underlying spots.  AAPL HOOD"
    )
    assert (
        explain_progress("2/2 mark  SKHY261016C00200000")
        == "Pricing each open position."
    )
    assert explain_progress("writing…") == "Saving the book snapshot."


def test_load_progress_page_uses_english_hint() -> None:
    page = format_load_progress(
        spin="⠋",
        label="1/2 fetch  spots · AAPL",
        done=0,
        total=4,
    )
    assert "⠋" in page
    assert "0/4" in page
    assert "Fetching live underlying spots." in page
    assert "AAPL" in page
    assert "1/2 fetch" not in page


def test_reveal_steps_are_header_rows_then_footer() -> None:
    steps = reveal_steps(2)
    assert steps == [
        DeskReveal(visible=0, footer=False),
        DeskReveal(visible=1, footer=False),
        DeskReveal(visible=2, footer=False),
        DeskReveal(visible=2, footer=True),
    ]


def test_reveal_interval_stays_near_one_second() -> None:
    assert reveal_interval_ms(4) == 40
    assert reveal_interval_ms(52) <= 20
    assert reveal_interval_ms(52) * 52 <= 1100


def _occs(group) -> list[str]:
    tables = [item for item in group.renderables if getattr(item, "columns", None)]
    names: list[str] = []
    for table in tables:
        names.extend(cell.plain for cell in table.columns[0].cells)
    return names


def _plains(group) -> list[str]:
    return [item.plain for item in group.renderables if hasattr(item, "plain")]


def test_reveal_paints_header_then_rows_then_footer() -> None:
    rows = [
        _row("HOOD261218C00150000", 3000.0, up=False),
        _row("AVGO261218C00500000", 4000.0, up=True),
        _row("CSCO261218C00130000", 1000.0, up=True),
        _row("INTC261016C00140000", 800.0, up=False),
    ]
    kwargs = {
        "account": "main",
        "feed": "alpaca",
        "refresh_sec": 15,
        "rows": rows,
        "realized": 50.0,
        "framed": False,
    }

    header = render_snapshot(**kwargs, reveal=DeskReveal(0, False))
    assert _occs(header) == []
    plains = _plains(header)
    assert any("[main]" in text for text in plains)
    assert not any("today +" in text for text in plains)
    assert not any("rPnL" in text for text in plains)
    assert not any("(no positions)" in text for text in plains)

    first = render_snapshot(**kwargs, reveal=DeskReveal(1, False))
    assert [name[:4] for name in _occs(first)] == ["AVGO"]
    first_plains = _plains(first)
    assert any("today +" in text for text in first_plains)
    assert not any("today −" in text or "today -" in text for text in first_plains)
    assert not any("rPnL" in text for text in first_plains)

    plus = render_snapshot(**kwargs, reveal=DeskReveal(2, False))
    assert [name[:4] for name in _occs(plus)] == ["AVGO", "CSCO"]

    minus_one = render_snapshot(**kwargs, reveal=DeskReveal(3, False))
    assert [name[:4] for name in _occs(minus_one)] == ["AVGO", "CSCO", "HOOD"]
    assert any("today −" in text or "today -" in text for text in _plains(minus_one))

    done = render_snapshot(**kwargs, reveal=DeskReveal(4, True))
    assert [name[:4] for name in _occs(done)] == ["AVGO", "CSCO", "HOOD", "INTC"]
    done_plains = _plains(done)
    assert any("rPnL" in text for text in done_plains)
    assert any("tPnL" in text for text in done_plains)
    tables = [item for item in done.renderables if getattr(item, "columns", None)]
    assert tables[-1].show_footer


def test_reserved_sections_keep_both_headers_and_two_slots() -> None:
    rows = [_row("AVGO261218C00500000", 4000.0, up=True)]
    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=rows,
        realized=10.0,
        framed=False,
        reserve_sections=True,
    )
    plains = _plains(group)
    assert any("today +" in text for text in plains)
    assert any("today −" in text or "today -" in text for text in plains)
    plus = next(text for text in plains if "today +" in text)
    minus = next(text for text in plains if "today −" in text or "today -" in text)
    assert "+200.00" in plus
    assert minus.strip() in {"today −", "today -"}
    tables = [item for item in group.renderables if getattr(item, "columns", None)]
    assert len(tables) == 2
    up_occs = [getattr(cell, "plain", cell) for cell in tables[0].columns[0].cells]
    down_occs = [getattr(cell, "plain", cell) for cell in tables[1].columns[0].cells]
    assert up_occs[0].startswith("AVGO")
    assert up_occs[1] == ""
    assert down_occs == ["", ""]
    assert not any("(no positions)" in name for name in up_occs + down_occs)


def test_reserved_sections_grow_past_two_rows() -> None:
    rows = [
        _row("AVGO261218C00500000", 4000.0, up=True),
        _row("CSCO261218C00130000", 1000.0, up=True),
        _row("MSFT261218C00400000", 900.0, up=True),
    ]
    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=rows,
        framed=False,
        reserve_sections=True,
    )
    tables = [item for item in group.renderables if getattr(item, "columns", None)]
    up_occs = [getattr(cell, "plain", cell) for cell in tables[0].columns[0].cells]
    down_occs = [getattr(cell, "plain", cell) for cell in tables[1].columns[0].cells]
    assert [name[:4] for name in up_occs] == ["AVGO", "CSCO", "MSFT"]
    assert down_occs == ["", ""]


def test_reveal_does_not_reserve_empty_section() -> None:
    rows = [_row("AVGO261218C00500000", 4000.0, up=True)]
    mid = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=rows,
        framed=False,
        reveal=DeskReveal(1, False),
        reserve_sections=True,
    )
    assert any("today +" in text for text in _plains(mid))
    assert not any("today −" in text or "today -" in text for text in _plains(mid))
