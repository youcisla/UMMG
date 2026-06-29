"""SQLite event log. All prompts and outputs land here.

Schema:
  events(
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL NOT NULL,         -- unix epoch seconds
    session_id   TEXT,                  -- client-supplied or generated
    role         TEXT NOT NULL,         -- 'user' | 'assistant' | 'system' | 'summary'
    model        TEXT,                  -- friendly model name (NULL for summaries)
    adapter      TEXT,                  -- adapter name (NULL for summaries)
    text         TEXT NOT NULL,
    latency_ms   REAL,                  -- assistant rows only
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    extra        TEXT                   -- JSON blob for future fields
  )

Indexes on (ts) and (session_id, ts).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    session_id  TEXT,
    role        TEXT    NOT NULL,
    model       TEXT,
    adapter     TEXT,
    text        TEXT    NOT NULL,
    latency_ms  REAL,
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    extra       TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts        ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_session   ON events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_role_ts   ON events(role, ts);
"""


@dataclass
class Event:
    role: str
    text: str
    ts: float
    session_id: str | None = None
    model: str | None = None
    adapter: str | None = None
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    extra: dict[str, Any] | None = None


class EventLog:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialized = False

    async def init(self) -> None:
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
        self._initialized = True

    async def append(self, event: Event) -> int:
        if not self._initialized:
            await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """INSERT INTO events
                   (ts, session_id, role, model, adapter, text, latency_ms,
                    tokens_in, tokens_out, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.ts,
                    event.session_id,
                    event.role,
                    event.model,
                    event.adapter,
                    event.text,
                    event.latency_ms,
                    event.tokens_in,
                    event.tokens_out,
                    json.dumps(event.extra) if event.extra else None,
                ),
            )
            await db.commit()
            rowid = cur.lastrowid
            assert rowid is not None
            return int(rowid)

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def count(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM events")
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def count_by_role(self, role: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM events WHERE role=?", (role,))
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def last_summary(self) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM events WHERE role='summary' ORDER BY ts DESC LIMIT 1"
            )
            row = await cur.fetchone()
        return dict(row) if row else None

    async def write_summary(self, text: str, ts: float | None = None) -> int:
        return await self.append(
            Event(role="summary", text=text, ts=ts or time.time())
        )