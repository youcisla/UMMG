"""LanceDB-backed event ledger.

Phase B of the storage migration: folds the SQLite event ledger into the same
LanceDB database that holds the vector store, so enabling
``memory.vector_backend: lancedb`` retires the SQLite/FAISS hybrid entirely.

Drop-in for the SQLite ``EventLog`` (memory/events.py): same async surface
(``init / append / recent / count / count_by_role / last_summary /
write_summary``) and the same ``Event`` dataclass.

Design for scale: LanceDB is the durable, append-only source of truth; hot
reads (recent window, counts, last summary) are served from small in-memory
caches rebuilt once at boot. This keeps the summarizer's frequent polling off
the disk path while still surviving restarts.

Note: LanceDB without the optional pylance/duckdb extras cannot push an ORDER BY
down to disk, so the one-time boot rebuild does a single full scan to seed the
caches. At single-user project-history scale this is negligible; if the ledger
ever grows to many millions of rows, add pylance+duckdb and stream the rebuild.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any

from .events import Event  # reuse the canonical dataclass

log = logging.getLogger("ummg.memory.lance_events")

_COLUMNS: tuple[str, ...] = (
    "id", "ts", "session_id", "role", "model", "adapter",
    "text", "latency_ms", "tokens_in", "tokens_out", "extra",
)


class LanceEventLog:
    def __init__(self, db_path: Path, table: str = "events", *, recent_cache: int = 512) -> None:
        self.db_path = Path(db_path)
        self.table_name = table
        self.recent_cache = recent_cache
        self._lock = asyncio.Lock()
        self._db: Any = None
        self._tbl: Any = None
        self._initialized = False
        # In-memory caches (durable data lives in Lance).
        self._next_id = 0
        self._total = 0
        self._by_role: dict[str, int] = {}
        self._last_summary: dict[str, Any] | None = None
        self._recent: deque[dict[str, Any]] = deque(maxlen=recent_cache)

    async def init(self) -> None:
        if self._initialized:
            return
        import lancedb
        import pyarrow as pa

        self.db_path.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self.db_path))

        schema = pa.schema(
            [
                pa.field("id", pa.int64()),
                pa.field("ts", pa.float64()),
                pa.field("session_id", pa.string()),
                pa.field("role", pa.string()),
                pa.field("model", pa.string()),
                pa.field("adapter", pa.string()),
                pa.field("text", pa.string()),
                pa.field("latency_ms", pa.float64()),
                pa.field("tokens_in", pa.int64()),
                pa.field("tokens_out", pa.int64()),
                pa.field("extra", pa.string()),
            ]
        )
        # Acquire the table without relying on a possibly-stale catalog listing:
        # open if present, otherwise create, and treat a create race as "open".
        opened = False
        try:
            self._tbl = self._db.open_table(self.table_name)
            opened = True
        except Exception:  # noqa: BLE001 - table likely doesn't exist yet
            try:
                self._tbl = self._db.create_table(self.table_name, schema=schema)
            except Exception:  # noqa: BLE001 - lost create race; open instead
                self._tbl = self._db.open_table(self.table_name)
                opened = True
        if opened:
            self._rebuild_caches()
        self._initialized = True

    def _rebuild_caches(self) -> None:
        """Seed in-memory caches from disk with a single scan (boot only)."""
        try:
            n = int(self._tbl.count_rows())
        except Exception:  # noqa: BLE001
            n = 0
        self._total = n
        if n == 0:
            self._next_id = 0
            return
        rows = self._tbl.search().limit(n).to_list()
        rows = [{k: r.get(k) for k in _COLUMNS} for r in rows]
        rows.sort(key=lambda r: (r.get("ts") or 0.0, r.get("id") or 0))
        self._next_id = max(int(r.get("id") or 0) for r in rows) + 1
        self._by_role = {}
        for r in rows:
            role = str(r.get("role") or "")
            self._by_role[role] = self._by_role.get(role, 0) + 1
        summaries = [r for r in rows if r.get("role") == "summary"]
        self._last_summary = summaries[-1] if summaries else None
        for r in rows[-self.recent_cache:]:
            self._recent.append(r)

    async def append(self, event: Event) -> int:
        if not self._initialized:
            await self.init()
        async with self._lock:
            event_id = self._next_id
            row = {
                "id": event_id,
                "ts": float(event.ts),
                "session_id": event.session_id,
                "role": event.role,
                "model": event.model,
                "adapter": event.adapter,
                "text": event.text,
                "latency_ms": event.latency_ms,
                "tokens_in": event.tokens_in,
                "tokens_out": event.tokens_out,
                "extra": json.dumps(event.extra) if event.extra else None,
            }
            self._tbl.add([row])
            # Update caches.
            self._next_id += 1
            self._total += 1
            self._by_role[event.role] = self._by_role.get(event.role, 0) + 1
            if event.role == "summary":
                self._last_summary = row
            self._recent.append(row)
            return event_id

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        # Most-recent-first, matching the SQLite EventLog contract.
        items = list(self._recent)[-limit:]
        return [dict(r) for r in reversed(items)]

    async def count(self) -> int:
        return self._total

    async def count_by_role(self, role: str) -> int:
        return self._by_role.get(role, 0)

    async def last_summary(self) -> dict[str, Any] | None:
        return dict(self._last_summary) if self._last_summary else None

    async def write_summary(self, text: str, ts: float | None = None) -> int:
        return await self.append(Event(role="summary", text=text, ts=ts or time.time()))
