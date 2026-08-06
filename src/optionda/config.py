from __future__ import annotations

import tomllib
from pathlib import Path

from optionda.models import AppConfig
from optionda.paths import ensure_home

try:
    import tomli_w
except ImportError:  # pragma: no cover - fallback writer
    tomli_w = None  # type: ignore[assignment]


def _config_path(home: Path) -> Path:
    return home / "config.toml"


def _dump_toml(data: dict) -> str:
    if tomli_w is not None:
        return tomli_w.dumps(data)
    # Minimal TOML writer for our flat config
    lines: list[str] = []

    def toml_value(value) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, (list, tuple)):
            return "[" + ", ".join(toml_value(item) for item in value) + "]"
        if isinstance(value, dict):
            return "{" + ", ".join(
                f'"{str(key)}" = {toml_value(item)}'
                for key, item in value.items()
            ) + "}"
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    for key, value in data.items():
        if value is None:
            continue
        lines.append(f"{key} = {toml_value(value)}")
    return "\n".join(lines) + ("\n" if lines else "")


def load_config(home: Path | None = None) -> AppConfig:
    root = ensure_home(home)
    path = _config_path(root)
    if not path.exists():
        cfg = AppConfig()
        save_config(cfg, root)
        return cfg
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    # Repair files written by pre-curve fallback writer, which serialized
    # empty collection defaults as strings.
    if raw.get("rate_curve") == "[]":
        raw["rate_curve"] = []
    if raw.get("dividend_yields") == "{}":
        raw["dividend_yields"] = {}
    return AppConfig.model_validate(raw)


def save_config(config: AppConfig, home: Path | None = None) -> Path:
    root = ensure_home(home)
    path = _config_path(root)
    payload = config.model_dump(exclude_none=True)
    path.write_text(_dump_toml(payload), encoding="utf-8")
    return path


def apply_feed_defaults(config: AppConfig, feed: str) -> AppConfig:
    """Set feed and poll interval defaults when switching providers."""
    if feed == "alpaca":
        return config.model_copy(update={"feed": "alpaca", "poll_interval_sec": 15})
    return config.model_copy(update={"feed": "yahoo", "poll_interval_sec": 60})


def rate_for_days(config: AppConfig, days: float) -> float:
    """Piecewise-linear zero-rate lookup; falls back to the flat desk rate."""
    curve = sorted((int(day), float(rate)) for day, rate in config.rate_curve)
    if not curve:
        return config.r
    if days <= curve[0][0]:
        return curve[0][1]
    if days >= curve[-1][0]:
        return curve[-1][1]
    for (left_day, left_rate), (right_day, right_rate) in zip(curve, curve[1:]):
        if left_day <= days <= right_day:
            ratio = (days - left_day) / (right_day - left_day)
            return left_rate + ratio * (right_rate - left_rate)
    return config.r


def dividend_for_symbol(config: AppConfig, symbol: str) -> float:
    return float(config.dividend_yields.get(symbol.strip().upper(), config.q))
