"""Per-request latency + status capture. Thread-safe ring buffer for /health."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class RequestRecord:
    ts: float
    model: str
    adapter: str
    status: int
    latency_ms: float
    tokens_in: int | None = None
    tokens_out: int | None = None
    memories_used: int | None = None


class LatencyTracker:
    def __init__(self, maxlen: int = 200) -> None:
        self._buf: deque[RequestRecord] = deque(maxlen=maxlen)
        self._lock = Lock()

    def record(self, rec: RequestRecord) -> None:
        with self._lock:
            self._buf.append(rec)

    def recent(self, n: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._buf)[-n:]
        return [r.__dict__ for r in items]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            items = list(self._buf)
        if not items:
            return {"count": 0}
        latencies = [r.latency_ms for r in items]
        return {
            "count": len(items),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2),
            "max_latency_ms": round(max(latencies), 2),
            "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
            "by_status": _count_by(items, lambda r: str(r.status)),
            "by_adapter": _count_by(items, lambda r: r.adapter),
        }


def _count_by(items: list[RequestRecord], key) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        k = key(it)
        out[k] = out.get(k, 0) + 1
    return out