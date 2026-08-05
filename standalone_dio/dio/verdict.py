"""Verdict helpers for building DIO context and stamping RuleResult metadata."""

from __future__ import annotations

from typing import Any

from .core import RuleResult
from .dataclasses import DioCommand, DioInspection


def inspection_from_result(rr: RuleResult | None) -> DioInspection | None:
    """Build DioInspection from a RuleResult (verdict condition only)."""
    if rr is None:
        return None
    meta = dict(rr.metadata or {})
    if "inspection_verdict" in meta:
        return DioInspection(
            inspection_id=str(meta.get("inspection_id", "")),
            verdict=str(meta.get("inspection_verdict", "UNKNOWN")),
            camera_verdicts={
                str(k): str(v).upper()
                for k, v in dict(meta.get("inspection_cameras") or {}).items()
            },
        )
    if getattr(rr, "camera_id", ""):
        return DioInspection(
            inspection_id="",
            verdict=str(getattr(rr.verdict, "value", rr.verdict)),
            camera_verdicts={
                str(rr.camera_id): str(
                    getattr(rr.verdict, "value", rr.verdict)
                ).upper()
            },
        )
    return None


def stamp_result_metadata(
    rr: RuleResult,
    commands: list[DioCommand],
    display: dict[str, Any] | None,
    *,
    held_pins: list[int] | None = None,
    di_count: int = 0,
    do_count: int = 8,
) -> None:
    """Attach DIO output to RuleResult metadata for sinks/logging."""
    rr.metadata = dict(rr.metadata or {})
    rr.metadata["dio_commands"] = [cmd.to_dict() for cmd in commands]
    rr.metadata["dio_command_count"] = len(commands)
    if display is not None:
        rr.metadata["dio_display"] = display
    if held_pins is not None:
        rr.metadata["dio_held_pins"] = list(held_pins)
    rr.metadata["di_count"] = di_count
    rr.metadata["do_count"] = do_count
