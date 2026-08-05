from __future__ import annotations

import tomllib
from pathlib import Path

from optionda.models import AppConfig
from optionda.paths import default_home, ensure_home

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
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key} = {value}")
        else:
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
    return "\n".join(lines) + ("\n" if lines else "")


def load_config(home: Path | None = None) -> AppConfig:
    root = ensure_home(home or default_home())
    path = _config_path(root)
    if not path.exists():
        cfg = AppConfig()
        save_config(cfg, root)
        return cfg
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return AppConfig.model_validate(raw)


def save_config(config: AppConfig, home: Path | None = None) -> Path:
    root = ensure_home(home or default_home())
    path = _config_path(root)
    payload = config.model_dump(exclude_none=True)
    path.write_text(_dump_toml(payload), encoding="utf-8")
    return path


def apply_feed_defaults(config: AppConfig, feed: str) -> AppConfig:
    """Set feed and poll interval defaults when switching providers."""
    if feed == "alpaca":
        return config.model_copy(update={"feed": "alpaca", "poll_interval_sec": 15})
    return config.model_copy(update={"feed": "yahoo", "poll_interval_sec": 60})
