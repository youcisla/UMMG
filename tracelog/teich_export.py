"""Teich formatter wrapper.

Teich (https://github.com/TeichAI/teich, PyPI `teich`) turns raw agent traces
into model-specific SFT datasets (chat-template rendering + response masking).
As of writing it is early-alpha (0.1.1a*), so EVERY call here is isolated behind
import + attribute guards: if teich is absent or its API has shifted, the
gateway keeps running and we simply skip dataset formatting — the raw masked
JSONL written by TraceRecorder remains the durable source of truth.

When you confirm the installed teich version, pin it in requirements.txt and,
if needed, adjust the call surface in `export_dataset` to match.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("ummg.trace.teich")


def teich_available() -> bool:
    try:
        import teich  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def export_dataset(traces_dir: Path, out_path: Path, *, tokenizer: str | None = None) -> bool:
    """Format raw .jsonl traces -> distillation dataset via Teich.

    Returns True on success, False if teich is unavailable or the call surface
    didn't match (logged, non-fatal). Never raises.
    """
    traces_dir = Path(traces_dir)
    out_path = Path(out_path)
    try:
        import teich  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log.warning("teich not installed (%s); raw traces in %s remain the dataset", exc, traces_dir)
        return False

    try:
        # Documented teich surface: load_traces() + format_and_mask().
        # Guarded with getattr so a renamed function degrades instead of crashing.
        load_traces = getattr(teich, "load_traces", None)
        format_and_mask = getattr(teich, "format_and_mask", None)
        if not callable(load_traces) or not callable(format_and_mask):
            log.warning(
                "teich present but load_traces/format_and_mask not found "
                "(version %s); skipping format step",
                getattr(teich, "__version__", "unknown"),
            )
            return False

        out_path.parent.mkdir(parents=True, exist_ok=True)
        traces = load_traces(str(traces_dir))
        format_and_mask(traces, output_path=str(out_path), tokenizer=tokenizer)
        log.info("teich dataset written -> %s", out_path)
        return True
    except TypeError as exc:
        # Signature drift in alpha API — report clearly, keep gateway alive.
        log.warning("teich call signature mismatch (%s); pin/adjust teich_export.export_dataset", exc)
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("teich export failed (%s); raw traces preserved", exc)
        return False
