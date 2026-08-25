from datetime import date, datetime, timezone

import pytest

from optionda.agent_view import (
    build_agent_view,
    format_agent_text,
    latest_path,
    load_latest,
    render_desk_html,
    write_latest,
)
from optionda.models import Position, RowMark


def _row(occ: str, notional: float, *, up: bool) -> RowMark:
    close = 10.0
    theo = 12.0 if up else 8.0
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
        upnl=200.0 if up else -200.0,
        close_spot=99.305,
        close_premium=close,
        theo_chg=theo - close,
        last_op_at=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
    )


def test_build_agent_view_splits_sections_and_totals() -> None:
    rows = [
        _row("AVGO261218C00500000", 4000.0, up=True),
        _row("INTC261016C00140000", 800.0, up=False),
    ]
    view = build_agent_view(
        account="main",
        feed="alpaca",
        rows=rows,
        realized=50.0,
        ts="2026-08-24T12:00:00+00:00",
    )
    assert view["account"] == "main"
    assert view["feed"] == "alpaca"
    assert view["n"] == 2
    assert view["rpnl"] == 50.0
    assert view["sum_model"] == 4800.0
    assert view["tpnl"] == 0.0
    assert [item["occ"][:4] for item in view["up"]] == ["AVGO"]
    assert [item["occ"][:4] for item in view["down"]] == ["INTC"]
    assert view["up"][0]["section"] == "+"
    assert view["down"][0]["section"] == "−"
    assert view["up"][0]["today"] == 200.0
    assert view["up"][0]["spot_chg_pct"] == pytest.approx(0.7, abs=0.05)
    assert view["up"][0]["theo_chg"] == pytest.approx(2.0)
    assert "email" not in view
    assert "password" not in view
    assert "smtp" not in str(view).lower()


def test_write_and_load_latest_roundtrip(tmp_path) -> None:
    view = build_agent_view(
        account="main",
        feed="alpaca",
        rows=[_row("AVGO261218C00500000", 4000.0, up=True)],
        realized=0.0,
    )
    path = write_latest(view, tmp_path)
    assert path == latest_path(tmp_path)
    loaded = load_latest(tmp_path)
    assert loaded is not None
    assert loaded["account"] == "main"
    assert loaded["up"][0]["occ"].startswith("AVGO")


def test_agent_text_is_a_desk_table() -> None:
    view = build_agent_view(
        account="main",
        feed="alpaca",
        rows=[
            _row("AVGO261218C00500000", 4000.0, up=True),
            _row("INTC261016C00140000", 800.0, up=False),
        ],
        realized=10.0,
    )
    text = format_agent_text(view)
    assert "today +" in text
    assert "today −" in text or "today -" in text
    assert "AVGO" in text
    assert "INTC" in text
    assert "Model$" in text
    assert "rPnL" in text
    assert "+0.7%" in text
    assert "+2.00" in text


def test_desk_html_looks_like_run() -> None:
    view = build_agent_view(
        account="main",
        feed="alpaca",
        rows=[_row("AVGO261218C00500000", 4000.0, up=True)],
        realized=10.0,
    )
    html = render_desk_html(view)
    assert "[main]" in html
    assert "optionda" in html
    assert "today +" in html
    assert "AVGO" in html
    assert "Model$" in html
    assert "+0.7%" in html
    assert "+2.00" in html
    assert "<img" not in html.lower()
    assert "On Mon" not in html
    assert "wrote:" not in html
