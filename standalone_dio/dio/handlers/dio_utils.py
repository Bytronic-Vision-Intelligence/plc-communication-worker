"""Shared helpers for local DIO handlers."""

from __future__ import annotations

from typing import Any, Dict

from dio.dataclasses import DioContext


def verdict_for_source(source: str, ctx: DioContext) -> str:
    if ctx.inspection is None:
        return "UNKNOWN"
    if source in ("overall", "verdict", "inspections"):
        return str(ctx.inspection.verdict).upper()
    return str(ctx.inspection.camera_verdicts.get(source, "UNKNOWN")).upper()


def set_do(pins: list[int], do_map: dict, condition: str, value: int = 1) -> None:
    pin_idx = do_map.get(condition)
    if pin_idx is None:
        return
    idx = int(pin_idx)
    if 0 <= idx < len(pins):
        pins[idx] = int(value)


def display_for_do(do_map: dict, pins: list[int]) -> dict[str, Any]:
    labels: Dict[int, str] = {}
    for condition, pin_idx in do_map.items():
        idx = int(pin_idx)
        if 0 <= idx < len(pins) and pins[idx]:
            labels[idx] = str(condition)
    return {
        "active_pins": [i for i, v in enumerate(pins) if v],
        "labels": labels,
        "pins": list(pins),
    }
