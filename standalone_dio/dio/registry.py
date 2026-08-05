"""Condition-based registry for DIO handlers."""

from __future__ import annotations

from enum import Enum
from typing import Callable, Dict, List, Optional

from .dataclasses import DioContext, DioHandlerSpec, DioOutput


class DioCondition(str, Enum):
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    CONFIG = "config"
    CAMERA_TRIGGER = "camera_trigger"
    RESULT_READY = "result_ready"
    INTERVAL = "interval"
    VERDICT = "verdict"
    DI = "di"


class DioOutputType(str, Enum):
    PULSE = "pulse"
    TOGGLE = "toggle"


DioHandler = Callable[[DioContext, dict], DioOutput]

_handlers: Dict[DioCondition, List[DioHandlerSpec]] = {c: [] for c in DioCondition}


def set_plugin_prefix(_plugin_id: Optional[str]) -> None:
    """Kept for load hooks; names are not used for dispatch."""


def register(
    *,
    on: DioCondition,
    type: DioOutputType = DioOutputType.PULSE,
    interval_s: float | None = None,
):
    """Decorator used by handler modules — appends a DioHandlerSpec."""

    def deco(func: DioHandler) -> DioHandler:
        spec = DioHandlerSpec(
            fn=func,
            condition=on,
            output_type=type,
            interval_s=float(interval_s) if interval_s is not None else None,
        )
        _handlers[on].append(spec)
        return func

    return deco


def handlers_for(condition: DioCondition) -> List[DioHandlerSpec]:
    return list(_handlers.get(condition, []))


def all_handlers() -> List[DioHandlerSpec]:
    out: List[DioHandlerSpec] = []
    for specs in _handlers.values():
        out.extend(specs)
    return out


def clear_handlers() -> None:
    """Reset registry (useful in tests / reloads)."""
    for key in list(_handlers):
        _handlers[key] = []
