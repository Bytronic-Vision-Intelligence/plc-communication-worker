"""Load and merge pin mappings (handlers YAML + config dio.pin_mapping)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

_CONFIG_PATH = Path(__file__).with_name("dio_configs.yaml")
_cached_base: Optional[dict] = None


def _load_plugin_yaml() -> dict:
    global _cached_base
    if _cached_base is None:
        if _CONFIG_PATH.is_file():
            with _CONFIG_PATH.open(encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
        else:
            loaded = {}
        _cached_base = loaded if isinstance(loaded, dict) else {}
    return copy.deepcopy(_cached_base)


def _deep_merge_dict(base: dict, override: Mapping[str, Any]) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def resolve_pin_mapping(profile_dio: Optional[dict] = None) -> dict:
    merged = _load_plugin_yaml()
    if profile_dio:
        override = profile_dio.get("pin_mapping")
        if isinstance(override, dict) and override:
            merged = _deep_merge_dict(merged, {"pin_mapping": override})
    pin_mapping = merged.get("pin_mapping") or {}
    return pin_mapping if isinstance(pin_mapping, dict) else {}
