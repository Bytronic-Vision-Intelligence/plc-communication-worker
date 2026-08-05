"""Standalone DIO package (adapted from packages/vs-dio, no monorepo deps)."""

from .core import RuleResult, Verdict
from .dataclasses import (
    DioCapabilities,
    DioCommand,
    DioContext,
    DioHandlerSpec,
    DioInspection,
    DioOutput,
)
from .display import build_vecow_display
from .registry import DioCondition, DioOutputType, handlers_for, register
from .service import DioService
from .dio_controller import VecowIO, default_dll_dir

__all__ = [
    "VecowIO",
    "default_dll_dir",
    "Verdict",
    "RuleResult",
    "DioCapabilities",
    "DioCommand",
    "DioCondition",
    "DioContext",
    "DioHandlerSpec",
    "DioInspection",
    "DioOutput",
    "DioOutputType",
    "DioService",
    "build_vecow_display",
    "handlers_for",
    "register",
]
