"""Load local DIO handler modules from dio/handlers/ (no pip plugin packages)."""

from __future__ import annotations

import importlib
import logging
from typing import Any, List, Set

logger = logging.getLogger(__name__)

_loaded_modules: Set[str] = set()


def _handler_names(cfg: Any) -> List[str]:
    """Modules listed under top-level `handlers:` or plugins.*.dio."""
    names: list[str] = []

    raw = getattr(cfg, "handlers", None)
    if raw is None and hasattr(cfg, "get"):
        raw = cfg.get("handlers")
    if isinstance(raw, str) and raw.strip():
        names.append(raw.strip())
    elif isinstance(raw, list):
        names.extend(str(n).strip() for n in raw if str(n).strip())

    plugins = getattr(cfg, "plugins", None)
    if plugins is None and hasattr(cfg, "get"):
        plugins = cfg.get("plugins")
    if isinstance(plugins, dict):
        for spec in plugins.values():
            if not isinstance(spec, dict):
                continue
            value = spec.get("dio")
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
            elif isinstance(value, list):
                names.extend(str(n).strip() for n in value if str(n).strip())
    elif plugins is not None and hasattr(plugins, "__dict__"):
        for spec in vars(plugins).values():
            if not hasattr(spec, "dio"):
                continue
            value = getattr(spec, "dio", None)
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
            elif isinstance(value, list):
                names.extend(str(n).strip() for n in value if str(n).strip())

    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def load_dio_plugins(cfg: Any, handlers_dir=None) -> List[str]:
    """Import handler modules under ``dio.handlers``; return basenames loaded.

    ``handlers_dir`` is accepted for API compatibility and ignored — modules
    live next to this package under ``dio/handlers/``.
    """
    del handlers_dir  # package-local only
    modules = _handler_names(cfg)
    if not modules:
        logger.info("No handlers listed in config — none loaded")
        return []

    loaded: list[str] = []
    for name in modules:
        if name in _loaded_modules:
            loaded.append(name)
            continue
        importlib.import_module(f"dio.handlers.{name}")
        _loaded_modules.add(name)
        loaded.append(name)
        logger.info("Loaded DIO handler module %s", name)

    logger.info("Loaded DIO handlers: %s", ", ".join(loaded))
    return loaded
