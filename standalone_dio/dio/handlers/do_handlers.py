"""Default DO handlers — map inspection verdict to pulse pins."""

from __future__ import annotations

import logging

from dio.dataclasses import DioCommand, DioContext, DioOutput
from dio.registry import DioCondition, DioOutputType, register

from .dio_config import resolve_pin_mapping
from .dio_utils import display_for_do, set_do, verdict_for_source

logger = logging.getLogger(__name__)


@register(on=DioCondition.VERDICT, type=DioOutputType.PULSE)
def do_verdict(ctx: DioContext, profile_dio: dict) -> DioOutput:
    """Pulse DO pins for PASS / FAIL on a completed inspection."""
    if ctx.inspection is None or ctx.rule_result is None:
        return DioOutput()

    meta = dict(ctx.rule_result.metadata or {})
    # Support either combined inspection stamp or per-camera results.
    overall = verdict_for_source("overall", ctx)
    if overall == "UNKNOWN" and "inspection_verdict" not in meta:
        overall = str(getattr(ctx.rule_result.verdict, "value", ctx.rule_result.verdict)).upper()

    pin_mapping = resolve_pin_mapping(profile_dio)
    do_map = pin_mapping.get("do") or {}
    if not isinstance(do_map, dict) or not do_map:
        return DioOutput()

    do_count = int(profile_dio.get("do_count", profile_dio.get("dio_count", 8)))
    pins = [0] * do_count

    if overall == "PASS":
        set_do(pins, do_map, "pass")
    if overall == "FAIL":
        set_do(pins, do_map, "fail")

    if not any(pins):
        return DioOutput()

    logger.info("DIO verdict=%s pins=%s", overall, [i for i, v in enumerate(pins) if v])
    return DioOutput(
        commands=[DioCommand(pins=pins)],
        display=display_for_do(do_map, pins),
    )
