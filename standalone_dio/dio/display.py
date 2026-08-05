"""Vecow HMI display helpers for separated DI / DO pin banks."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


def _pad_pins(pins: Optional[Sequence[int]], count: int) -> list[int]:
    count = max(0, int(count))
    vec = [int(v) for v in list(pins or [])[:count]]
    if len(vec) < count:
        vec.extend([0] * (count - len(vec)))
    return vec


def _bank(
    *,
    count: int,
    pins: Optional[Sequence[int]] = None,
    labels: Optional[Mapping[Any, Any]] = None,
) -> dict[str, Any]:
    pin_list = _pad_pins(pins, count)
    label_map: dict[int, str] = {}
    if labels:
        for key, value in labels.items():
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < count:
                label_map[idx] = str(value)
    return {
        "count": count,
        "pins": pin_list,
        "active": [i for i, v in enumerate(pin_list) if v],
        "labels": label_map,
    }


def build_vecow_display(
    *,
    di_count: int,
    do_count: int,
    do_pins: Optional[Sequence[int]] = None,
    di_pins: Optional[Sequence[int]] = None,
    do_labels: Optional[Mapping[Any, Any]] = None,
    di_labels: Optional[Mapping[Any, Any]] = None,
) -> dict[str, Any]:
    return {
        "di": _bank(count=di_count, pins=di_pins, labels=di_labels),
        "do": _bank(count=do_count, pins=do_pins, labels=do_labels),
    }
