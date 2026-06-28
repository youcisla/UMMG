"""Top-K retrieval. Wraps the FAISS store with embedding-then-search."""
from __future__ import annotations

from typing import Any

import numpy as np

from .embed import Embedder
from .store import VectorStore


class Retriever:
    def __init__(self, store: VectorStore, embedder: Embedder, top_k: int = 6) -> None:
        self.store = store
        self.embedder = embedder
        self.top_k = top_k

    async def retrieve(self, query: str) -> list[dict[str, Any]]:
        vec = await self.embedder.embed(query)
        if vec is None:
            return []
        return self.store.search(vec, top_k=self.top_k)