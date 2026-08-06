from datetime import date, datetime, timezone

import pytest

from optionda.display.surface_plot import (
    _cached_heatmap,
    default_deltas,
    expiries_for_plot,
    heatmap_for_grid,
    open_plotly_surface,
    open_plotly_surfaces,
    render_surface_panels,
    sample_iv_grid,
    show_figure_in_browser,
)
from optionda.models import Position, RowMark
from optionda.pricing.surface import ExpirySmile, IvSurface, SurfaceNode


def _surface() -> IvSurface:
    return IvSurface(
        underlying="SPCX",
        spot=116.0,
        as_of=datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
        source="test",
        smiles=[
            ExpirySmile(
                expiry=date(2026, 9, 18),
                nodes=[
                    SurfaceNode(strike=130, delta=-0.6, iv=1.05),
                    SurfaceNode(strike=110, delta=-0.35, iv=0.92),
                    SurfaceNode(strike=100, delta=-0.20, iv=0.86),
                    SurfaceNode(strike=90, delta=-0.10, iv=0.80),
                ],
            ),
            ExpirySmile(
                expiry=date(2026, 12, 18),
                nodes=[
                    SurfaceNode(strike=130, delta=-0.55, iv=0.98),
                    SurfaceNode(strike=100, delta=-0.22, iv=0.84),
                    SurfaceNode(strike=80, delta=-0.08, iv=0.78),
                ],
            ),
        ],
        quality={"accepted": 7, "rejected": 0},
    )


def test_sample_iv_grid_shape() -> None:
    grid = sample_iv_grid(_surface(), deltas=default_deltas(5), max_expiries=2)
    assert grid is not None
    assert len(grid.expiries) == 2
    assert len(grid.deltas) == 5
    assert len(grid.ivs) == 2
    assert all(len(row) == 5 for row in grid.ivs)
    assert grid.iv_min <= grid.iv_max


def test_heatmap_cached_and_nonempty() -> None:
    grid = sample_iv_grid(_surface(), deltas=default_deltas(7), max_expiries=2)
    assert grid is not None
    _cached_heatmap.cache_clear()
    first = heatmap_for_grid(grid, width=40, height=8)
    second = heatmap_for_grid(grid, width=40, height=8)
    assert first == second
    assert len(first) > 50
    assert _cached_heatmap.cache_info().hits >= 1


def test_render_surface_panels_skips_stale() -> None:
    surface = _surface()
    # Force stale as_of
    stale = IvSurface(
        underlying=surface.underlying,
        spot=surface.spot,
        as_of=datetime(2020, 1, 1, tzinfo=timezone.utc),
        source=surface.source,
        smiles=surface.smiles,
        quality=surface.quality,
    )
    pos = Position(
        occ_symbol="SPCX260918P00100000",
        underlying="SPCX",
        expiry=date(2026, 9, 18),
        strike=100,
        option_type="put",
        qty=1,
        side="long",
        iv_frozen=0.9,
        iv_as_of=datetime.now(timezone.utc),
        entry_premium=6.0,
    )
    rows = [
        RowMark(
            position=pos,
            spot=116.0,
            theo=7.0,
            delta=-0.26,
            dte=40,
            notional=700,
            surface_iv=0.91,
            valuation_mode="surface",
        )
    ]
    panels = render_surface_panels({"SPCX": stale}, rows, compact=True)
    assert panels == []


def test_expiries_for_plot_prefers_book_then_fills() -> None:
    surface = _surface()
    chosen = expiries_for_plot(
        surface, prefer=[date(2026, 9, 18)], max_expiries=2
    )
    assert chosen == [date(2026, 9, 18), date(2026, 12, 18)]


def test_plotly_figure_builds() -> None:
    plotly = pytest.importorskip("plotly")
    _ = plotly
    grid = sample_iv_grid(_surface(), deltas=default_deltas(5), max_expiries=2)
    assert grid is not None
    fig = open_plotly_surface(grid)
    assert fig.data[0].type == "surface"
    assert len(fig.data[0].z) >= 2


def test_plotly_multi_panel_grid() -> None:
    pytest.importorskip("plotly")
    surface = _surface()
    g1 = sample_iv_grid(surface, deltas=default_deltas(5), max_expiries=2)
    g2 = sample_iv_grid(
        IvSurface(
            underlying="AAPL",
            spot=200.0,
            as_of=surface.as_of,
            source="test",
            smiles=surface.smiles,
            quality=surface.quality,
        ),
        deltas=default_deltas(5),
        max_expiries=2,
    )
    assert g1 is not None and g2 is not None
    fig = open_plotly_surfaces([(g1, ()), (g2, ())])
    surfaces = [trace for trace in fig.data if trace.type == "surface"]
    assert len(surfaces) == 2
    assert "scene2" in fig.layout


def test_show_figure_writes_html_without_blocking(tmp_path, monkeypatch) -> None:
    import tempfile

    pytest.importorskip("plotly")
    grid = sample_iv_grid(_surface(), deltas=default_deltas(5), max_expiries=2)
    assert grid is not None
    fig = open_plotly_surface(grid)
    opened: list[str] = []
    monkeypatch.setattr(
        "webbrowser.open", lambda url, *a, **k: opened.append(url) or True
    )
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    path = show_figure_in_browser(fig)
    assert opened and opened[0].startswith("file:")
    assert path.endswith("optionda-iv-surfaces.html")
    assert (tmp_path / "optionda-iv-surfaces.html").is_file()


def test_plotly_rejects_single_expiry_mesh() -> None:
    pytest.importorskip("plotly")
    grid = sample_iv_grid(
        _surface(),
        expiries=[date(2026, 9, 18)],
        deltas=default_deltas(5),
        max_expiries=1,
    )
    assert grid is not None
    assert len(grid.expiries) == 1
    with pytest.raises(RuntimeError, match="≥2 expiries"):
        open_plotly_surface(grid)
