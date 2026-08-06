from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any, Sequence

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from optionda.models import RowMark
from optionda.pricing.surface import (
    IvSurface,
    is_surface_fresh,
    sticky_delta_iv,
    _interpolate_iv_by_delta,
)

# Compact desk charts — keep small so Live refresh stays cheap.
RUN_MAX_UNDERLYINGS = 2
RUN_MAX_EXPIRIES = 4
RUN_DELTA_BUCKETS = 9
RUN_PLOT_WIDTH = 62
RUN_PLOT_HEIGHT = 9

FULL_MAX_EXPIRIES = 12
FULL_DELTA_BUCKETS = 13
FULL_PLOT_WIDTH = 78
FULL_PLOT_HEIGHT = 16
# Denser mesh for browser 3D (Plotly needs ≥2 expiries to draw a surface).
PLOTLY_MAX_EXPIRIES = 16
PLOTLY_DELTA_BUCKETS = 21


def expiries_for_plot(
    surface: IvSurface,
    *,
    prefer: Sequence[date] | None = None,
    max_expiries: int = PLOTLY_MAX_EXPIRIES,
) -> list[date]:
    """Pick expiries for a 3D mesh: keep book dates, then fill across the smile."""
    available = [smile.expiry for smile in surface.smiles]
    if not available:
        return []
    chosen: list[date] = []
    for expiry in prefer or ():
        if expiry in available and expiry not in chosen:
            chosen.append(expiry)
    for expiry in available:
        if expiry not in chosen:
            chosen.append(expiry)
        if len(chosen) >= max_expiries:
            break
    return sorted(chosen)


@dataclass(frozen=True)
class SurfaceMarker:
    occ_symbol: str
    expiry: date
    delta: float
    iv: float


@dataclass(frozen=True)
class IvGrid:
    underlying: str
    as_of: datetime
    source: str
    deltas: tuple[float, ...]
    expiries: tuple[date, ...]
    ivs: tuple[tuple[float | None, ...], ...]  # [expiry][delta]
    iv_min: float
    iv_max: float


def default_deltas(n: int = RUN_DELTA_BUCKETS) -> tuple[float, ...]:
    if n < 2:
        return (-0.5, 0.5)
    # Symmetric signed-delta grid covering typical OTM/ATM option deltas.
    step = 1.4 / (n - 1)
    return tuple(round(-0.7 + i * step, 3) for i in range(n))


def sample_iv_grid(
    surface: IvSurface,
    *,
    expiries: Sequence[date] | None = None,
    deltas: Sequence[float] | None = None,
    max_expiries: int = RUN_MAX_EXPIRIES,
) -> IvGrid | None:
    """Resample smile nodes onto a regular expiry×delta mesh."""
    if not surface.smiles:
        return None
    delta_axis = tuple(deltas) if deltas is not None else default_deltas()
    if expiries is not None:
        wanted = list(expiries)
        smile_map = {smile.expiry: smile for smile in surface.smiles}
        rows = [smile_map[exp] for exp in wanted if exp in smile_map]
    else:
        rows = list(surface.smiles)
    if not rows:
        return None
    rows = rows[:max_expiries]

    matrix: list[tuple[float | None, ...]] = []
    values: list[float] = []
    for smile in rows:
        line: list[float | None] = []
        for delta in delta_axis:
            iv = _interpolate_iv_by_delta(list(smile.nodes), delta)
            line.append(iv)
            if iv is not None:
                values.append(iv)
        matrix.append(tuple(line))
    if not values:
        return None
    return IvGrid(
        underlying=surface.underlying,
        as_of=surface.as_of,
        source=surface.source,
        deltas=delta_axis,
        expiries=tuple(smile.expiry for smile in rows),
        ivs=tuple(matrix),
        iv_min=min(values),
        iv_max=max(values),
    )


def markers_for_rows(
    surface: IvSurface,
    rows: Sequence[RowMark],
    *,
    rate: float = 0.045,
    dividend: float = 0.0,
    now: datetime | None = None,
) -> list[SurfaceMarker]:
    """Build legend markers for book legs on this underlying."""
    from optionda.pricing.bs import years_to_expiry

    current = now or datetime.now(timezone.utc)
    out: list[SurfaceMarker] = []
    for row in rows:
        pos = row.position
        if pos.underlying != surface.underlying:
            continue
        if row.spot is None or row.spot <= 0:
            continue
        years = years_to_expiry(pos.expiry, current)
        iv = row.surface_iv
        if iv is None:
            iv = sticky_delta_iv(
                surface,
                pos,
                spot=row.spot,
                years=years,
                rate=rate,
                dividend=dividend,
            )
        if iv is None:
            continue
        # Approximate delta from the mark when available.
        delta = row.delta if row.delta is not None else 0.0
        # Prefer absolute option delta sign from the contract type for legend.
        if row.delta is None:
            delta = -0.25 if pos.option_type == "put" else 0.25
        out.append(
            SurfaceMarker(
                occ_symbol=pos.occ_symbol,
                expiry=pos.expiry,
                delta=float(delta),
                iv=float(iv),
            )
        )
    return out


def _matrix_for_plot(grid: IvGrid) -> list[list[float]]:
    """Fill gaps with iv_min so plotext matrix_plot stays dense and fast."""
    fill = grid.iv_min
    return [
        [(cell if cell is not None else fill) * 100.0 for cell in row]
        for row in grid.ivs
    ]


def render_heatmap_plotext(
    grid: IvGrid,
    *,
    title: str | None = None,
    width: int = RUN_PLOT_WIDTH,
    height: int = RUN_PLOT_HEIGHT,
) -> str:
    """Render IV mesh via plotext (no pandas). Returns ANSI string."""
    import plotext as plt

    matrix = _matrix_for_plot(grid)
    label = title or (
        f"{grid.underlying} IV%  "
        f"{grid.iv_min * 100:.0f}–{grid.iv_max * 100:.0f}  "
        f"asof={grid.as_of.strftime('%m-%d %H:%MZ')}"
    )
    plt.clf()
    plt.matrix_plot(matrix)
    plt.plotsize(max(24, width), max(6, height))
    plt.title(label)
    # Axis hints (plotext matrix has no native categorical ticks).
    plt.xlabel("delta →  " + "  ".join(f"{d:+.1f}" for d in grid.deltas))
    y_labels = " | ".join(exp.strftime("%y%m%d") for exp in grid.expiries)
    plt.ylabel(y_labels)
    return plt.build()


@lru_cache(maxsize=32)
def _cached_heatmap(
    underlying: str,
    as_of_key: str,
    source: str,
    deltas: tuple[float, ...],
    expiries: tuple[str, ...],
    ivs_key: tuple[tuple[float | None, ...], ...],
    iv_min: float,
    iv_max: float,
    width: int,
    height: int,
) -> str:
    grid = IvGrid(
        underlying=underlying,
        as_of=datetime.fromisoformat(as_of_key),
        source=source,
        deltas=deltas,
        expiries=tuple(date.fromisoformat(item) for item in expiries),
        ivs=ivs_key,
        iv_min=iv_min,
        iv_max=iv_max,
    )
    return render_heatmap_plotext(grid, width=width, height=height)


def heatmap_for_grid(
    grid: IvGrid,
    *,
    width: int = RUN_PLOT_WIDTH,
    height: int = RUN_PLOT_HEIGHT,
) -> str:
    as_of = grid.as_of
    if as_of.tzinfo is None:
        as_of_key = as_of.replace(tzinfo=timezone.utc).isoformat()
    else:
        as_of_key = as_of.isoformat()
    return _cached_heatmap(
        grid.underlying,
        as_of_key,
        grid.source,
        grid.deltas,
        tuple(exp.isoformat() for exp in grid.expiries),
        grid.ivs,
        grid.iv_min,
        grid.iv_max,
        width,
        height,
    )


def render_surface_panels(
    surfaces: dict[str, IvSurface | None],
    rows: Sequence[RowMark],
    *,
    now: datetime | None = None,
    compact: bool = True,
    max_underlyings: int = RUN_MAX_UNDERLYINGS,
) -> list[Panel]:
    """Build Rich panels. Safe to call once per mark and reuse across Live ticks."""
    current = now or datetime.now(timezone.utc)
    held = sorted({row.position.underlying for row in rows})
    panels: list[Panel] = []
    shown = 0
    for underlying in held:
        if shown >= max_underlyings:
            break
        surface = surfaces.get(underlying)
        if surface is None or not is_surface_fresh(surface, current):
            continue
        held_expiries = sorted(
            {
                row.position.expiry
                for row in rows
                if row.position.underlying == underlying
            }
        )
        max_exp = RUN_MAX_EXPIRIES if compact else FULL_MAX_EXPIRIES
        n_delta = RUN_DELTA_BUCKETS if compact else FULL_DELTA_BUCKETS
        grid = sample_iv_grid(
            surface,
            expiries=held_expiries or None,
            deltas=default_deltas(n_delta),
            max_expiries=max_exp,
        )
        if grid is None:
            # Fall back to first calibrated expiries when held expiry missing on surface.
            grid = sample_iv_grid(
                surface,
                deltas=default_deltas(n_delta),
                max_expiries=max_exp,
            )
        if grid is None:
            continue
        width = RUN_PLOT_WIDTH if compact else FULL_PLOT_WIDTH
        height = RUN_PLOT_HEIGHT if compact else FULL_PLOT_HEIGHT
        chart = heatmap_for_grid(grid, width=width, height=height)
        markers = markers_for_rows(surface, rows, now=current)
        age_h = (current - surface.as_of).total_seconds() / 3600.0
        legend_bits = [
            Text(
                f"age={age_h:.1f}h  nodes≈{sum(len(s.nodes) for s in surface.smiles)}  "
                f"src={surface.source}",
                style="dim",
            )
        ]
        for marker in markers[:4]:
            legend_bits.append(
                Text(
                    f"▲ {marker.occ_symbol}  δ={marker.delta:+.2f}  "
                    f"IV*={marker.iv * 100:.1f}%",
                    style="cyan",
                )
            )
        if len(markers) > 4:
            legend_bits.append(Text(f"+{len(markers) - 4} more legs", style="dim"))
        panels.append(
            Panel(
                Group(Text(chart), *legend_bits),
                title=f"surface {underlying}",
                title_align="left",
                border_style="magenta",
                padding=(0, 1),
            )
        )
        shown += 1

    skipped = len(held) - shown
    if skipped > 0 and panels:
        panels.append(
            Panel(
                Text(
                    f"+{skipped} more underlying(s) — run: optionda surface <TICKER>",
                    style="dim",
                ),
                border_style="dim",
            )
        )
    return panels


def _require_plotly():
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "plotly is required for surface 3D; install with: pip install 'optionda[viz]'"
        ) from exc
    return go, make_subplots


def _surface_xyz(grid: IvGrid) -> tuple[list[float], list[int], list[list[float]], list[str]]:
    if len(grid.expiries) < 2:
        raise RuntimeError(
            f"{grid.underlying}: need ≥2 expiries for a 3D surface "
            "(re-run refresh-iv or pass a richer surface)"
        )
    fill = grid.iv_min * 100.0
    z = [
        [(cell * 100.0 if cell is not None else fill) for cell in row]
        for row in grid.ivs
    ]
    x = [float(d) for d in grid.deltas]
    y = list(range(len(grid.expiries)))
    y_text = [exp.isoformat() for exp in grid.expiries]
    return x, y, z, y_text


def _marker_xyz(
    grid: IvGrid, markers: Sequence[SurfaceMarker]
) -> tuple[list[float], list[int], list[float], list[str]]:
    mx, my, mz, labels = [], [], [], []
    exp_index = {exp: i for i, exp in enumerate(grid.expiries)}
    for marker in markers:
        if marker.expiry not in exp_index:
            continue
        mx.append(marker.delta)
        my.append(exp_index[marker.expiry])
        mz.append(marker.iv * 100.0)
        labels.append(marker.occ_symbol)
    return mx, my, mz, labels


def _grid_cols(n: int) -> int:
    if n <= 1:
        return 1
    if n == 2:
        return 2
    return 3


def open_plotly_surfaces(
    panels: Sequence[tuple[IvGrid, Sequence[SurfaceMarker]]],
) -> Any:
    """One browser figure: multi-row/column 3D surfaces for all underlyings."""
    go, make_subplots = _require_plotly()
    if not panels:
        raise RuntimeError("no surfaces to plot")

    from math import ceil

    n = len(panels)
    cols = _grid_cols(n)
    rows = ceil(n / cols)
    titles = [
        (
            f"{grid.underlying}  "
            f"{len(grid.expiries)}×{len(grid.deltas)}  "
            f"{grid.as_of.strftime('%m-%d %H:%MZ')}"
        )
        for grid, _ in panels
    ]
    # Pad titles for empty cells
    while len(titles) < rows * cols:
        titles.append("")

    specs: list[list[dict[str, str] | None]] = []
    for r in range(rows):
        row_specs: list[dict[str, str] | None] = []
        for c in range(cols):
            idx = r * cols + c
            row_specs.append({"type": "surface"} if idx < n else None)
        specs.append(row_specs)

    fig = make_subplots(
        rows=rows,
        cols=cols,
        specs=specs,
        subplot_titles=titles,
        horizontal_spacing=0.05,
        # Extra gap so bottom-row 3D axis labels are not clipped.
        vertical_spacing=0.10,
    )

    scene_layout: dict[str, Any] = {}
    for index, (grid, markers) in enumerate(panels):
        row = index // cols + 1
        col = index % cols + 1
        x, y, z, y_text = _surface_xyz(grid)
        z_min = min(min(row_z) for row_z in z)
        z_max = max(max(row_z) for row_z in z)
        surface_kwargs: dict[str, Any] = {
            "x": x,
            "y": y,
            "z": z,
            "colorscale": "Viridis",
            "showscale": index == 0,
            "opacity": 0.88,
            "connectgaps": True,
            "hovertemplate": (
                f"{grid.underlying}<br>"
                "Δ=%{x:.2f}<br>expiry=%{customdata}<br>IV=%{z:.1f}%"
                "<extra></extra>"
            ),
            "customdata": [[y_text[i]] * len(x) for i in range(len(y))],
            "name": grid.underlying,
        }
        if index == 0:
            surface_kwargs["colorbar"] = {"title": "IV %", "x": 1.02}
        fig.add_trace(go.Surface(**surface_kwargs), row=row, col=col)
        mx, my, mz, labels = _marker_xyz(grid, markers)
        if mx:
            # Lift markers slightly above the mesh so short-dated edge points
            # (often y≈0) stay visible instead of sinking into the surface.
            z_pad = max(1.5, 0.03 * (z_max - z_min + 1e-9))
            mz_lifted = [value + z_pad for value in mz]
            hover = [
                f"{label}<br>expiry={y_text[yi] if 0 <= yi < len(y_text) else '?'}"
                for label, yi in zip(labels, my)
            ]
            fig.add_trace(
                go.Scatter3d(
                    x=mx,
                    y=my,
                    z=mz_lifted,
                    mode="markers",
                    marker={
                        "size": 7,
                        "symbol": "diamond",
                        "color": "rgba(255, 214, 102, 0.98)",
                        "line": {"color": "rgba(15, 15, 15, 0.9)", "width": 1.5},
                    },
                    hovertemplate=(
                        "<b>%{customdata}</b><br>"
                        "Δ=%{x:.2f}<br>IV=%{z:.1f}%<extra>book</extra>"
                    ),
                    customdata=hover,
                    name=f"{grid.underlying} book",
                    showlegend=False,
                ),
                row=row,
                col=col,
            )
            titles[index] = (
                f"{grid.underlying}  "
                f"{len(grid.expiries)}×{len(grid.deltas)}  "
                f"{grid.as_of.strftime('%m-%d %H:%MZ')}  ·  "
                f"{len(mx)} book"
            )
        scene_key = "scene" if index == 0 else f"scene{index + 1}"
        # Pad axis ranges so first/last expiry markers are not clipped by the box.
        y_hi = max(y) if y else 1
        x_lo, x_hi = (min(x), max(x)) if x else (-0.7, 0.7)
        x_span = max(x_hi - x_lo, 0.2)
        z_lo = min(z_min, min(mz) if mz else z_min)
        z_hi = max(z_max, max(mz) if mz else z_max)
        z_span = max(z_hi - z_lo, 1.0)
        scene_layout[scene_key] = {
            "xaxis_title": "delta",
            "yaxis_title": "expiry",
            "zaxis_title": "IV %",
            "xaxis": {"range": [x_lo - 0.08 * x_span, x_hi + 0.08 * x_span]},
            "yaxis": {
                "tickmode": "array",
                "tickvals": y,
                "ticktext": y_text,
                "range": [-0.45, y_hi + 0.45],
            },
            "zaxis": {"range": [z_lo - 0.08 * z_span, z_hi + 0.12 * z_span]},
            "aspectmode": "manual",
            "aspectratio": {"x": 1.2, "y": 1.0, "z": 0.7},
            "camera": {
                "eye": {"x": 1.55, "y": -1.55, "z": 1.05},
            },
        }

    # Subplot titles are set at construction; refresh when book counts known.
    for i, text in enumerate(titles[:n]):
        anno_i = i
        if anno_i < len(fig.layout.annotations):
            fig.layout.annotations[anno_i].text = text

    fig.update_layout(
        title=f"optionda IV surfaces ({n})  ·  1 panel / underlying (expiry on Y)",
        margin={"l": 12, "r": 56, "t": 64, "b": 36},
        autosize=True,
        # Tall enough that the last row's 3D frames are not cropped.
        height=max(640, 560 * rows),
        showlegend=False,
        **scene_layout,
    )
    return fig


def open_plotly_surface(
    grid: IvGrid,
    markers: Sequence[SurfaceMarker] = (),
) -> Any:
    """Single-underlying convenience wrapper around ``open_plotly_surfaces``."""
    return open_plotly_surfaces([(grid, markers)])


def show_figure_in_browser(fig: Any) -> str:
    """Write HTML once and open it — does not block on a local HTTP server."""
    import tempfile
    import webbrowser
    from pathlib import Path

    path = Path(tempfile.gettempdir()) / "optionda-iv-surfaces.html"
    # Embed a full-bleed shell so Plotly can use the entire viewport width.
    html = fig.to_html(
        include_plotlyjs=True,
        full_html=False,
        default_width="100%",
        default_height="100%",
        config={"responsive": True},
    )
    path.write_text(
        (
            "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
            "<title>optionda IV surfaces</title>"
            "<style>"
            "html,body{margin:0;padding:0;width:100%;min-height:100%;background:#111;}"
            ".wrap{box-sizing:border-box;width:100vw;min-height:100vh;"
            "padding:12px 12px 48px;}"
            ".wrap .plotly-graph-div{width:100%!important;}"
            "</style></head><body><div class='wrap'>"
            f"{html}"
            "</div></body></html>"
        ),
        encoding="utf-8",
    )
    webbrowser.open(path.as_uri())
    return str(path)
