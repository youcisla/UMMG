"""One-shot migration: SQLite event ledger (events.db) -> LanceDB events table.

Copies every row in ts/id order into the unified LanceDB database, completing
the Phase B fold so the SQLite/FAISS hybrid can be retired. Vectors migrate
separately via migrate_faiss_to_lance.py.

Run on the Windows host, gateway stopped, from the repo root:

    python scripts/migrate_events_to_lance.py
    python scripts/migrate_events_to_lance.py --dry-run

Uses stdlib sqlite3 to read (no aiosqlite needed). Refuses to run if the target
Lance events table is non-empty unless --force.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import load_settings  # noqa: E402
from memory.events import Event  # noqa: E402
from memory.lance_events import LanceEventLog  # noqa: E402


async def _run() -> int:
    ap = argparse.ArgumentParser(description="Migrate SQLite events -> LanceDB")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    settings = load_settings()
    db_path = settings.data_dir / "events.db"
    if not db_path.exists():
        print(f"FAIL: no events.db at {db_path} (nothing to migrate)", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ts, session_id, role, model, adapter, text, latency_ms, "
        "tokens_in, tokens_out, extra FROM events ORDER BY ts ASC, id ASC"
    ).fetchall()
    conn.close()
    print(f"source: {len(rows)} events in {db_path.name}")

    if args.dry_run:
        print("dry-run: no writes performed")
        return 0

    ledger = LanceEventLog(settings.data_dir / "lance", table="events")
    await ledger.init()
    if await ledger.count() > 0 and not args.force:
        print(f"FAIL: target events table already has {await ledger.count()} rows; "
              f"pass --force to append", file=sys.stderr)
        return 1

    migrated = 0
    for r in rows:
        extra = None
        if r["extra"]:
            try:
                extra = json.loads(r["extra"])
            except Exception:  # noqa: BLE001
                extra = {"_raw": r["extra"]}
        await ledger.append(Event(
            role=r["role"], text=r["text"], ts=float(r["ts"]),
            session_id=r["session_id"], model=r["model"], adapter=r["adapter"],
            latency_ms=r["latency_ms"], tokens_in=r["tokens_in"],
            tokens_out=r["tokens_out"], extra=extra,
        ))
        migrated += 1
        if migrated % 1000 == 0:
            print(f"  migrated {migrated}/{len(rows)}")

    print(f"OK: migrated {migrated} events into LanceDB (size now {await ledger.count()})")
    print("Next: set memory.vector_backend: lancedb in models.yaml and restart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
