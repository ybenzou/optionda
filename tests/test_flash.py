from datetime import date, datetime, timezone

from optionda.display.table import (
    _STATUS_WIDTH,
    _dir_style,
    _fit_width,
    _inline_bar,
    spinner_frame,
    _iv_asof_label,
    _model_cell,
    _model_iv_cell,
    _money_flash,
    _move_direction,
    _pnl_flash,
    _premium_chg,
    _spot_cell,
    _spot_chg_pct,
    format_poll_status,
    partition_desk_rows,
    render_snapshot,
    sort_desk_rows,
)
from optionda.models import Position, RowMark


def test_move_direction():
    assert _move_direction(10.0, 9.0) == 1
    assert _move_direction(9.0, 10.0) == -1
    assert _move_direction(10.0, 10.0) == 0
    assert _move_direction(None, 1.0) == 0


def test_drop_flashes_red_even_when_pnl_still_positive():
    # Absolute uPnL still green (+100), but tick dropped from +120 → flash red.
    text = _pnl_flash(100.0, 120.0, phase="hot")
    assert "on dark_red" in text.style
    assert "+100.00" in text.plain


def test_rise_flashes_green_background():
    text = _money_flash(5.25, 5.00, phase="hot")
    assert "on dark_green" in text.style
    assert "5.25" in text.plain


def test_idle_keeps_soft_direction_tint():
    up = _money_flash(5.25, 5.00, phase="idle")
    down = _money_flash(4.80, 5.00, phase="idle")
    assert up.style == "green"
    assert down.style == "red"


def test_dir_style_phases():
    assert "on dark_green" in _dir_style(1, phase="hot")
    assert _dir_style(-1, phase="warm") == "bold red"
    assert _dir_style(0, phase="hot") == "dim"


def test_poll_status_width_is_stable():
    fetch = format_poll_status(
        "1/2 fetch  spots · AAPL AVGO CSCO GOOG HOOD IBM INTC",
        busy=True,
        done=0,
        total=1,
    )
    mark = format_poll_status(
        "2/2 mark  SKHY261016C00200000",
        busy=True,
        done=3,
        total=11,
    )
    idle = format_poll_status("5s", busy=False, eta_sec=5)
    assert fetch == _fit_width("1/2 fetch   0/1  spots")
    assert "spots ·" not in fetch
    assert "SKHY261016C00200000" in mark
    assert len(fetch) == _STATUS_WIDTH
    assert len(mark) == _STATUS_WIDTH
    assert len(idle) == _STATUS_WIDTH


def test_chrome_plain_hides_when_idle() -> None:
    from optionda.display.table import format_chrome_plain

    idle = format_chrome_plain(poll_busy=False, eta_sec=5, poll_label="5s")
    assert idle == ""
    busy = format_chrome_plain(
        spin="⠹",
        poll_busy=True,
        poll_label="2/2 mark  ready",
        poll_done=10,
        poll_total=10,
    )
    assert "⠹" in busy
    assert "10/10" in busy
    assert "#" in busy
    assert "fetch" not in busy
    assert "mark" not in busy


def test_spinner_frame_cycles() -> None:
    assert spinner_frame(0) == "⠋"
    assert spinner_frame(1) == "⠙"
    assert spinner_frame(0) != spinner_frame(1)
    assert spinner_frame(10) == spinner_frame(0)


def test_inline_bar_fills():
    empty = _inline_bar(0.0, width=10)
    full = _inline_bar(1.0, width=10)
    busy = _inline_bar(0.5, width=10, busy=True)
    assert empty.plain == "-" * 10
    assert full.plain == "#" * 10
    assert busy.plain == "#####-----"
    assert any(span.style == "bold yellow" for span in busy.spans)


def test_spot_chg_pct_vs_close():
    up = _spot_chg_pct(146.81, 141.65)
    assert up is not None
    assert "+3.6%" in up.plain
    assert "green" in str(up.style)

    down = _spot_chg_pct(140.0, 141.65)
    assert down is not None
    assert "-1.2%" in down.plain
    assert "red" in str(down.style)

    cell = _spot_cell(146.81, 141.65, None, phase="idle")
    assert "146.81" in cell.plain
    assert "+3.6%" in cell.plain


def test_today_model_pnl_sums_paren_moves() -> None:
    from optionda.display.table import render_snapshot, today_model_pnl

    long = Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350.0,
        option_type="call",
        qty=2,
        side="long",
        iv_frozen=0.25,
        iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
        entry_premium=3.5,
    )
    short = Position(
        occ_symbol="IBM261218C00300000",
        underlying="IBM",
        expiry=date(2026, 12, 18),
        strike=300.0,
        option_type="call",
        qty=1,
        side="short",
        iv_frozen=0.40,
        iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
        entry_premium=5.8,
    )
    long_row = RowMark(
        position=long,
        spot=210.0,
        theo=12.0,
        delta=0.3,
        dte=90.0,
        notional=2400.0,
        cost=3.5,
        upnl=1700.0,
        close_premium=10.8,
        theo_chg=1.2,
    )
    short_row = RowMark(
        position=short,
        spot=180.0,
        theo=4.5,
        delta=-0.2,
        dte=120.0,
        notional=-450.0,
        cost=5.8,
        upnl=130.0,
        close_premium=4.7,
        theo_chg=-0.2,
    )
    assert today_model_pnl(long_row) == 240.0
    assert today_model_pnl(short_row) == 20.0
    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=[long_row, short_row],
        realized=100.0,
        framed=False,
    )
    table = next(
        item for item in group.renderables if getattr(item, "columns", None)
    )
    model_footer = table.columns[6].footer
    model_text = model_footer.plain if hasattr(model_footer, "plain") else str(model_footer)
    assert "tPnL" not in model_text
    assert "1,950.00" in model_text
    plains = [
        item.plain
        for item in group.renderables
        if hasattr(item, "plain")
    ]
    assert any("tPnL" in text and "+260.00" in text for text in plains)
    assert any("rPnL" in text and "+100.00" in text for text in plains)


def test_model_cell_shows_dollar_chg_vs_close():
    up = _premium_chg(12.00, 10.80)
    assert up is not None
    assert "+1.20" in up.plain
    assert "green" in str(up.style)

    down = _premium_chg(10.45, 10.80)
    assert down is not None
    assert "-0.35" in down.plain
    assert "red" in str(down.style)

    cell = _model_cell(12.00, 10.80, None, phase="idle")
    assert "12.00" in cell.plain
    assert "+1.20" in cell.plain


def test_model_iv_shows_et_session_date():
    as_of = datetime(2026, 8, 12, 20, 5, tzinfo=timezone.utc)  # 16:05 ET
    label = _iv_asof_label(as_of)
    assert label == "8/12"

    cell = _model_iv_cell(0.779, as_of, stale=False)
    assert "77.9%" in cell.plain
    assert "8/12" not in cell.plain

    stale = _model_iv_cell(0.40, as_of, stale=True)
    assert "8/12" in stale.plain
    assert any(span.style == "bold yellow" for span in stale.spans)


def test_continuous_desk_has_no_bottom_live_caption() -> None:
    pos = Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350.0,
        option_type="call",
        qty=1,
        side="long",
        iv_frozen=0.25,
        iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
        entry_premium=3.5,
    )
    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=[
            RowMark(
                position=pos,
                spot=305.0,
                theo=3.8,
                delta=0.19,
                dte=98.0,
                notional=380.0,
                cost=3.5,
                upnl=30.0,
            )
        ],
        continuous=True,
        spin="⠹",
    )
    panel = group.renderables[0]
    table = panel.renderable.renderables[-1]
    assert table.caption is None


def test_session_notes_stay_inside_snapshot() -> None:
    pos = Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350.0,
        option_type="call",
        qty=1,
        side="long",
        iv_frozen=0.25,
        iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
        entry_premium=3.5,
    )
    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=[
            RowMark(
                position=pos,
                spot=305.0,
                theo=3.8,
                delta=0.19,
                dte=98.0,
                notional=380.0,
                cost=3.5,
                upnl=30.0,
            )
        ],
        continuous=True,
        notes=[
            "completed session 8/14",
            "surface AAPL IV 8/18",
            "surface CSCO IV 8/18",
            "close AAPL 210.00 (alpaca)",
            "close pending AAPL: late print",
        ],
    )
    texts = [item.plain for item in group.renderables[0].renderable.renderables if hasattr(item, "plain")]
    assert not any("completed session" in text for text in texts)
    assert not any("surface AAPL IV" in text for text in texts)
    assert not any("surface CSCO IV" in text for text in texts)
    assert not any("close AAPL 210.00" in text for text in texts)
    assert any("close pending AAPL: late print" in text for text in texts)


def test_session_date_sits_on_the_title_line() -> None:
    pos = Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350.0,
        option_type="call",
        qty=1,
        side="long",
        iv_frozen=0.25,
        iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
        entry_premium=3.5,
    )
    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=[
            RowMark(
                position=pos,
                spot=305.0,
                theo=3.8,
                delta=0.19,
                dte=98.0,
                notional=380.0,
                cost=3.5,
                upnl=30.0,
                surface_session_date=date(2026, 8, 14),
                reference_session_date=date(2026, 8, 14),
            )
        ],
        continuous=True,
        framed=False,
    )
    title = group.renderables[0]
    assert "[main]" in title.plain
    assert "optionda" in title.plain
    assert "8/14" in title.plain
    assert "IV 8/14" not in title.plain
    assert "close 8/14" not in title.plain
    body = " ".join(
        item.plain for item in group.renderables[1:] if hasattr(item, "plain")
    )
    assert "IV 8/14" not in body
    assert "close 8/14" not in body


def test_unframed_snapshot_keeps_title_on_first_line() -> None:
    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=[],
        continuous=True,
        framed=False,
    )
    assert "[main]" in group.renderables[0].plain
    assert "optionda" in group.renderables[0].plain


def test_busy_header_uses_spinner_not_bar() -> None:
    pos = Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350.0,
        option_type="call",
        qty=1,
        side="long",
        iv_frozen=0.25,
        iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
        entry_premium=3.5,
    )
    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=[
            RowMark(
                position=pos,
                spot=305.0,
                theo=3.8,
                delta=0.19,
                dte=98.0,
                notional=380.0,
                cost=3.5,
                upnl=30.0,
            )
        ],
        continuous=True,
        poll_busy=True,
        poll_label="1/2 fetch",
        poll_done=0,
        poll_total=1,
        spin="/",
        header_bar=False,
    )
    meta = group.renderables[0].renderable.renderables[0]
    assert "alpaca" not in meta.plain
    assert "/" in meta.plain
    assert "#" not in meta.plain
    assert "-" not in meta.plain


def _row(occ: str, notional: float | None) -> RowMark:
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
        theo=None if notional is None else notional / 100.0,
        delta=0.2,
        dte=90.0,
        notional=notional,
        cost=3.5,
        upnl=None,
        error="no spot" if notional is None else None,
    )


def test_desk_rows_sort_by_abs_notional() -> None:
    ordered = sort_desk_rows(
        [
            _row("CSCO261218C00130000", 1086.0),
            _row("HOOD261218C00150000", 909.0),
            _row("AVGO261218C00500000", 3350.0),
            _row("BADX261218C00100000", None),
        ]
    )
    assert [row.position.occ_symbol[:4] for row in ordered] == [
        "AVGO",
        "CSCO",
        "HOOD",
        "BADX",
    ]


def test_desk_hides_delta_and_keeps_last() -> None:
    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=[_row("AVGO261218C00500000", 3350.0)],
        framed=False,
    )
    table = next(
        item for item in group.renderables if getattr(item, "columns", None)
    )
    headers = [col.header for col in table.columns]
    assert "Delta" not in headers
    assert headers[-1] == "Last"
    assert headers[-2] == "DTE"


def test_desk_splits_today_up_then_down() -> None:
    up_big = _row("AVGO261218C00500000", 4000.0)
    up_small = _row("CSCO261218C00130000", 1000.0)
    down_big = _row("HOOD261218C00150000", 3000.0)
    down_small = _row("INTC261016C00140000", 800.0)
    up_big = up_big.model_copy(update={"close_premium": 38.0, "theo": 40.0, "theo_chg": 2.0})
    up_small = up_small.model_copy(update={"close_premium": 9.0, "theo": 10.0, "theo_chg": 1.0})
    down_big = down_big.model_copy(update={"close_premium": 32.0, "theo": 30.0, "theo_chg": -2.0})
    down_small = down_small.model_copy(
        update={"close_premium": 9.0, "theo": 8.0, "theo_chg": -1.0}
    )
    up, down = partition_desk_rows([down_small, up_small, down_big, up_big])
    assert [row.position.occ_symbol[:4] for row in up] == ["AVGO", "CSCO"]
    assert [row.position.occ_symbol[:4] for row in down] == ["HOOD", "INTC"]

    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=[down_small, up_small, down_big, up_big],
        framed=False,
    )
    labels = [item.plain for item in group.renderables if hasattr(item, "plain")]
    plus_line = next(text for text in labels if "today +" in text)
    minus_line = next(
        text for text in labels if "today −" in text or "today -" in text
    )
    assert "+300.00" in plus_line
    assert "-300.00" in minus_line
    plus_at = next(i for i, text in enumerate(labels) if "today +" in text)
    minus_at = next(
        i for i, text in enumerate(labels) if "today −" in text or "today -" in text
    )
    assert plus_at > 0 and labels[plus_at - 1] == ""
    assert minus_at > 0 and labels[minus_at - 1] == ""
    tables = [item for item in group.renderables if getattr(item, "columns", None)]
    assert len(tables) == 2
    up_occs = [cell.plain for cell in tables[0].columns[0].cells]
    down_occs = [cell.plain for cell in tables[1].columns[0].cells]
    assert up_occs[0].startswith("AVGO")
    assert up_occs[1].startswith("CSCO")
    assert down_occs[0].startswith("HOOD")
    assert down_occs[1].startswith("INTC")
