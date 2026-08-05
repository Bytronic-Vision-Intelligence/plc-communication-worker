"""Minimal types that replace vs_core for standalone use."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


@dataclass
class RuleResult:
    """Lightweight result container used by DIO handlers/service."""

    frame_index: int = 0
    timestamp: float = field(default_factory=time.monotonic)
    verdict: Verdict | str = Verdict.UNKNOWN
    triggered_rule: str = ""
    camera_id: str = ""
    sku_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


ExecHook = Callable[[str, str], None]


@dataclass(frozen=True)
class PluginDomain:
    """How to load plugin-style modules (local filesystem in standalone)."""

    config_key: str
    resolve_package_dir: Callable[[Path, str], Path]
    matches_package_dir: Callable[[str], bool]
    module_label: str = "plugin"
    warn_empty_modules: bool = False
    log_empty_config: bool = True
    log_summary: bool = False
    on_before_exec: Optional[ExecHook] = None
    on_after_exec: Optional[ExecHook] = None
