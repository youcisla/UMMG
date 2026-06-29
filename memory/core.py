"""Memory core facade. Wires events + store + embed + retrieve + summarize
into a single object the router can call.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .events import EventLog, Event
from .store import VectorStore
from .embed import Embedder
from .retrieve import Retriever
from .summarize import SummarizerWorker
from config import MemoryConfig

log = logging.getLogger("ummg.memory.core")


@dataclass
class MemoryWriteResult:
    user_event_id: int
    assistant_event_id: int | None
    embedded_user: bool
    embedded_assistant: bool


class MemoryCore:
    def __init__(self, cfg: MemoryConfig, data_dir: Path, *,
                 anthropic_upstream: str, anthropic_api_key: str) -> None:
        self.cfg = cfg
        self.data_dir = data_dir
        if cfg.vector_backend == "lancedb":
            from .lance_events import LanceEventLog
            self.events = LanceEventLog(data_dir / "lance", table="events")
        else:
            self.events = EventLog(data_dir / "events.db")
        self.embedder = Embedder(
            primary_base_url=cfg.embedding_primary_base_url,
            primary_model=cfg.embedding_primary_model,
            fallback_model=cfg.embedding_fallback_model,
        )
        self.store: VectorStore | None = None
        self.retriever: Retriever | None = None
        self.summarizer: SummarizerWorker | None = None
        self._anthropic_upstream = anthropic_upstream
        self._anthropic_api_key = anthropic_api_key

    async def init(self) -> None:
        await self.events.init()
        backend = await self.embedder.init()
        if self.cfg.vector_backend == "lancedb":
            from .lance_store import LanceVectorStore
            self.store = LanceVectorStore(
                db_path=self.data_dir / "lance",
                table=self.cfg.lance_table,
                dim=backend.dim,
                index_threshold=self.cfg.lance_index_threshold,
            )
        else:
            self.store = VectorStore(
                index_path=self.data_dir / "vectors.faiss",
                payloads_path=self.data_dir / "vectors.payloads.json",
                dim=backend.dim,
            )
        self.store.init()
        self.retriever = Retriever(self.store, self.embedder, top_k=self.cfg.top_k)
        self.summarizer = SummarizerWorker(
            self.events,
            self.store,
            self.embedder,
            every_n=self.cfg.summary_every_n_events,
            window=self.cfg.summary_window,
            summary_model=self.cfg.summary_model,
            anthropic_upstream=self._anthropic_upstream,
            anthropic_api_key=self._anthropic_api_key,
        )
        await self.summarizer.start()
        log.info(
            "memory core initialized: events=%d, vectors=%d, backend=%s",
            await self.events.count(),
            self.store.size(),
            backend.name,
        )

    async def shutdown(self) -> None:
        if self.summarizer:
            await self.summarizer.stop()

    async def pre_routing(
        self,
        *,
        model: str,
        session_id: str | None,
        user_text: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Write user event + return (retrieved_memories, latest_summary).

        Called BEFORE the adapter call. Always writes the user event first.
        """
        await self.events.append(Event(
            role="user",
            text=user_text,
            ts=time.time(),
            session_id=session_id,
            model=model,
        ))
        # Embed + upsert user text (best-effort).
        if self.store and self.retriever:
            vec = await self.embedder.embed(user_text)
            if vec is not None:
                self.store.add(vec, {
                    "role": "user",
                    "text": user_text,
                    "ts": time.time(),
                    "session_id": session_id,
                    "model": model,
                })
            memories = await self.retriever.retrieve(user_text)
        else:
            memories = []
        latest = await self.events.last_summary()
        return memories, latest

    async def post_routing(
        self,
        *,
        model: str,
        adapter: str,
        session_id: str | None,
        assistant_text: str,
        latency_ms: float,
        tokens_in: int | None,
        tokens_out: int | None,
    ) -> int | None:
        """Write assistant event after the model returns."""
        event_id = await self.events.append(Event(
            role="assistant",
            text=assistant_text,
            ts=time.time(),
            session_id=session_id,
            model=model,
            adapter=adapter,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        ))
        if self.store:
            vec = await self.embedder.embed(assistant_text)
            if vec is not None:
                self.store.add(vec, {
                    "role": "assistant",
                    "text": assistant_text,
                    "ts": time.time(),
                    "session_id": session_id,
                    "model": model,
                    "adapter": adapter,
                })
        return event_id

    def stats(self) -> dict[str, Any]:
        return {
            "store_size": self.store.size() if self.store else 0,
            "data_dir": str(self.data_dir),
        }