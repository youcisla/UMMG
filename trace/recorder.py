"""Non-blocking trajectory recorder.

Design constraints (from UMMG directives):
  * MUST NOT add latency to the request path. record() only enqueues; all I/O
    happens in a background task.
  * MUST mask secrets BEFORE anything touches disk (bearer token, x-api-key,
    Authorization, provider api keys, sk-/sk-ant- style tokens).
  * MUST fail open: a full queue drops the trace with a warning; it never
    raises into the router.

Output: one JSON object per line in ``<dir>/traces-YYYYMMDD.jsonl`` — the raw
source-of-truth format Teich then consumes (see teich_export.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("ummg.trace")

# Substring match (case-insensitive) on dict keys to redact.
_SECRET_KEY_HINTS: tuple[str, ...] = (
    "authorization", "api_key", "apikey", "api-key", "x-api-key",
    "bearer", "secret", "token", "password",
)
# Value patterns that look like credentials even under a benign key.
_SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|sk-ant-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+)")
_REDACTED = "***REDACTED***"


def _mask(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact secret-looking keys and values. Bounded depth."""
    if _depth > 12:
        return obj
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if any(h in str(k).lower() for h in _SECRET_KEY_HINTS):
                out[k] = _REDACTED
            else:
                out[k] = _mask(v, _depth + 1)
        return out
    if isinstance(obj, list):
        return [_mask(v, _depth + 1) for v in obj]
    if isinstance(obj, str):
        return _SECRET_VALUE_RE.sub(_REDACTED, obj)
    return obj


class TraceRecorder:
    def __init__(
        self,
        out_dir: Path,
        *,
        enabled: bool = False,
        queue_max: int = 1000,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.enabled = enabled
        self.queue_max = queue_max
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0

    async def start(self) -> None:
        if not self.enabled:
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._queue = asyncio.Queue(maxsize=self.queue_max)
        self._task = asyncio.create_task(self._drain(), name="ummg-trace-drain")
        log.info("trace recorder started -> %s", self.out_dir)

    async def stop(self) -> None:
        if self._task is None:
            return
        # Signal drain to finish, then wait briefly for it to flush.
        if self._queue is not None:
            await self._queue.put({"__stop__": True})
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        if self._dropped:
            log.warning("trace recorder dropped %d traces (queue full)", self._dropped)

    def record(self, trajectory: dict[str, Any]) -> None:
        """Enqueue a trajectory. Never blocks, never raises."""
        if not self.enabled or self._queue is None:
            return
        try:
            self._queue.put_nowait(trajectory)
        except asyncio.QueueFull:
            self._dropped += 1
        except Exception as exc:  # noqa: BLE001 - capture must never break routing
            log.debug("trace enqueue failed: %s", exc)

    async def _drain(self) -> None:
        assert self._queue is not None
        while True:
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                return
            if item.get("__stop__"):
                return
            try:
                self._write(item)
            except Exception as exc:  # noqa: BLE001
                log.debug("trace write failed: %s", exc)

    def _write(self, trajectory: dict[str, Any]) -> None:
        record = _mask(trajectory)
        record.setdefault("ts", time.time())
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = self.out_dir / f"traces-{day}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
