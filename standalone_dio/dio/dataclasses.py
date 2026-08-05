"""Data contracts for DIO command mapping and transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from .core import RuleResult

if TYPE_CHECKING:
    from .registry import DioCondition, DioHandler, DioOutputType


@dataclass
class DioCommand:
    """Single DIO write command: pin vector + pulse/toggle."""

    pins: list[int]
    type: "DioOutputType | str" = "pulse"

    def to_dict(self) -> dict[str, Any]:
        raw_type = self.type
        type_val = getattr(raw_type, "value", raw_type)
        return {
            "pins": [int(v) for v in self.pins],
            "type": str(type_val),
        }


@dataclass
class DioOutput:
    """Mapper output for one condition emission."""

    commands: list[DioCommand] = field(default_factory=list)
    display: dict[str, Any] | None = None

    @property
    def is_empty(self) -> bool:
        return not self.commands


@dataclass
class DioInspection:
    """Combined inspection verdict context across one or more cameras."""

    inspection_id: str = ""
    verdict: str = "UNKNOWN"
    camera_verdicts: Dict[str, str] = field(default_factory=dict)


@dataclass
class DioCapabilities:
    """Host-injected helpers for handlers."""

    web_up: Optional[Callable[[], bool]] = None
    processes_alive: Optional[Callable[[], bool]] = None
    publish_command: Optional[Callable[[dict], None]] = None


@dataclass
class DioContext:
    """Input context passed to DIO handlers when a condition fires."""

    condition: "DioCondition | str"
    rule_result: RuleResult | None = None
    inspection: DioInspection | None = None
    camera_id: str | None = None
    capabilities: DioCapabilities = field(default_factory=DioCapabilities)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DioHandlerSpec:
    """Registration record for a handler."""

    fn: "DioHandler"
    condition: "DioCondition"
    output_type: "DioOutputType"
    interval_s: float | None = None
