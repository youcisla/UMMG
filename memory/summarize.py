"""Rolling summarizer worker.

Every N assistant events, takes the last K events, asks the configured
summary_model (via the Anthropic adapter) to produce a concise state
summary, writes it as a `summary` row in SQLite, and upserts it into FAISS.

This worker runs as an asyncio task started in main.py's lifespan.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from .events import EventLog
from .store import VectorStore
from .embed import Embedder

log = logging.getLogger("ummg.memory.summarize")


class SummarizerWorker:
    def __init__(
        self,
        events: EventLog,
        store: VectorStore,
        embedder: Embedder,
        *,
        every_n: int,
        window: int,
        summary_model: str,
        anthropic_upstream: str,
        anthropic_api_key: str,
    ) -> None:
        self.events = events
        self.store = store
        self.embedder = embedder
        self.every_n = max(1, every_n)
        self.window = max(2, window)
        self.summary_model = summary_model
        self.anthropic_upstream = anthropic_upstream.rstrip("/")
        self.anthropic_api_key = anthropic_api_key
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_summary_at_count = 0
        self._poll_seconds = 5.0

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="ummg-summarizer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.maybe_summarize()
            except Exception:
                log.exception("summarizer iteration failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def maybe_summarize(self) -> bool:
        # Only count assistant events (user/assistant pairs).
        n = await self._count_assistant()
        if n - self._last_summary_at_count < self.every_n:
            return False
        recent = await self.events.recent(limit=self.window)
        if not recent:
            return False
        transcript = self._format_transcript(recent)
        new_summary = await self._call_summarizer(transcript)
        if not new_summary:
            return False
        ts = time.time()
        await self.events.write_summary(new_summary, ts=ts)
        # Embed and upsert into FAISS so retrievals can surface summaries too.
        vec = await self.embedder.embed(new_summary)
        if vec is not None:
            self.store.add(
                vec,
                {
                    "role": "summary",
                    "text": new_summary,
                    "ts": ts,
                },
            )
        self._last_summary_at_count = n
        log.info("summarizer wrote summary at event_count=%d", n)
        return True

    async def _count_assistant(self) -> int:
        # Backend-agnostic: works for both SQLite and LanceDB ledgers.
        return await self.events.count_by_role("assistant")

    def _format_transcript(self, rows: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for r in reversed(rows):  # chronological
            role = r.get("role") or "?"
            text = (r.get("text") or "").strip()
            if not text:
                continue
            lines.append(f"[{role}] {text[:1000]}")
        return "\n".join(lines)

    async def _call_summarizer(self, transcript: str) -> str | None:
        prompt = (
            "You are a conversation-state compressor. Produce a concise "
            "rolling summary (max ~400 words) that preserves: the active "
            "task, decisions made, open questions, user preferences, and "
            "any facts the user asked to be remembered. Output ONLY the "
            "summary, no preamble.\n\nTRANSCRIPT:\n" + transcript
        )
        payload = {
            "model": self.summary_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 800,
            "temperature": 0.2,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.anthropic_api_key:
            headers["x-api-key"] = self.anthropic_api_key
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    f"{self.anthropic_upstream}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
            if r.status_code >= 400:
                log.warning("summarizer call HTTP %d: %s", r.status_code, r.text[:200])
                return None
            data = r.json()
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                log.warning("summarizer response malformed: %s", json.dumps(data)[:200])
                return None
        except Exception as exc:
            log.warning("summarizer call failed: %s", exc)
            return None