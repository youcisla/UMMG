"""Trace subsystem.

Captures raw agent trajectories (request + response) off the hot path and,
optionally, formats them into distillation-ready datasets via Teich.

Public surface:
    TraceRecorder  - non-blocking capture + masked JSONL writer
    export_dataset - Teich-backed formatter (degrades gracefully if absent)
"""
from __future__ import annotations

from .recorder import TraceRecorder
from .teich_export import export_dataset, teich_available

__all__ = ["TraceRecorder", "export_dataset", "teich_available"]
