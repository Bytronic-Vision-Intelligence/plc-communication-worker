"""YAML config loading for the app_v2 DIO package."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import yaml

APP_V2_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = APP_V2_ROOT / "config.yaml"


def _to_ns(value: Any) -> Any:
    if isinstance(value, Mapping):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_to_ns(v) for v in value]
    return value


def load_config(path: str | Path | None = None) -> SimpleNamespace:
    """Load YAML into an attribute-accessible namespace (getattr-friendly)."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.is_file():
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {cfg_path}")
    return _to_ns(data)


def config_as_dict(cfg: Any) -> dict[str, Any]:
    """Best-effort convert config object back to a plain dict."""
    if isinstance(cfg, dict):
        return cfg
    if isinstance(cfg, SimpleNamespace):
        out: dict[str, Any] = {}
        for key, value in vars(cfg).items():
            out[key] = config_as_dict(value) if isinstance(value, SimpleNamespace) else value
        return out
    return {}
