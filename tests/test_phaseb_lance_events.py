"""Phase B verification — LanceEventLog parity with the SQLite EventLog."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("lancedb")

from memory.events import Event  # noqa: E402
from memory.lance_events import LanceEventLog  # noqa: E402


def test_append_recent_counts_summary_and_persistence() -> None:
    async def run() -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lance"
            led = LanceEventLog(path, table="events")
            await led.init()

            ids = []
            ids.append(await led.append(Event(role="user", text="hello", ts=1.0, session_id="s1")))
            ids.append(await led.append(Event(role="assistant", text="hi", ts=2.0,
                                              session_id="s1", model="glm-5.2", adapter="zai")))
            ids.append(await led.append(Event(role="assistant", text="more", ts=3.0,
                                              session_id="s1", model="claude-sonnet", adapter="anthropic")))
            assert ids == [0, 1, 2]

            assert await led.count() == 3
            assert await led.count_by_role("assistant") == 2
            assert await led.count_by_role("user") == 1

            await led.write_summary("state summary", ts=4.0)
            ls = await led.last_summary()
            assert ls is not None and ls["text"] == "state summary"
            assert await led.count_by_role("summary") == 1

            recent = await led.recent(limit=2)
            # most-recent-first
            assert [r["text"] for r in recent] == ["state summary", "more"]

            # Reopen: caches must rebuild from disk (durability + correct counts).
            led2 = LanceEventLog(path, table="events")
            await led2.init()
            assert await led2.count() == 4
            assert await led2.count_by_role("assistant") == 2
            ls2 = await led2.last_summary()
            assert ls2 is not None and ls2["text"] == "state summary"
            # next append continues id sequence without collision
            new_id = await led2.append(Event(role="user", text="again", ts=5.0))
            assert new_id == 4

    asyncio.run(run())
