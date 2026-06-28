"""Embedding client.

Primary: OpenAI-compatible /v1/embeddings endpoint (probe at boot).
Fallback: sentence-transformers/all-MiniLM-L6-v2 (loaded lazily).

The chosen backend's vector dimension is reported via .dim so the
FAISS index can be sized correctly.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np

log = logging.getLogger("ummg.memory.embed")


@dataclass
class EmbeddingBackend:
    name: str
    dim: int


class Embedder:
    def __init__(
        self,
        primary_base_url: str,
        primary_model: str,
        fallback_model: str,
    ) -> None:
        self.primary_base_url = primary_base_url.rstrip("/")
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self._backend: EmbeddingBackend | None = None
        self._st_model: Any = None  # lazy-loaded sentence-transformers

    async def init(self) -> EmbeddingBackend:
        if self._backend is not None:
            return self._backend

        probed = await self._probe_primary()
        if probed is not None:
            self._backend = probed
            log.info("embedding backend = primary (%s, dim=%d)", self.primary_model, probed.dim)
            return probed

        # Fallback path — load sentence-transformers in a thread (it blocks).
        log.warning(
            "primary embedding endpoint unreachable; loading fallback %s",
            self.fallback_model,
        )
        dim = await asyncio.to_thread(self._load_fallback)
        self._backend = EmbeddingBackend(name="fallback", dim=dim)
        log.info("embedding backend = fallback (%s, dim=%d)", self.fallback_model, dim)
        return self._backend

    async def _probe_primary(self) -> EmbeddingBackend | None:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.post(
                    f"{self.primary_base_url}/v1/embeddings",
                    json={"input": "ping", "model": self.primary_model},
                    headers={"Content-Type": "application/json"},
                )
        except Exception as exc:
            log.info("primary embed probe failed: %s", exc)
            return None
        if r.status_code >= 400:
            log.info("primary embed probe HTTP %d: %s", r.status_code, r.text[:200])
            return None
        try:
            data = r.json()
            vec = data["data"][0]["embedding"]
            return EmbeddingBackend(name="primary", dim=len(vec))
        except Exception as exc:
            log.info("primary embed response unparseable: %s", exc)
            return None

    def _load_fallback(self) -> int:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(self.fallback_model)
        self._st_model = model
        # Probe dim by embedding a throwaway string.
        v = model.encode(["dim-probe"], convert_to_numpy=True)
        return int(v.shape[1])

    async def embed(self, text: str) -> np.ndarray | None:
        if self._backend is None:
            await self.init()
        assert self._backend is not None

        if self._backend.name == "primary":
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    r = await client.post(
                        f"{self.primary_base_url}/v1/embeddings",
                        json={"input": text, "model": self.primary_model},
                        headers={"Content-Type": "application/json"},
                    )
                r.raise_for_status()
                vec = r.json()["data"][0]["embedding"]
                arr = np.asarray(vec, dtype=np.float32)
                if arr.shape[0] != self._backend.dim:
                    log.warning(
                        "primary embedding dim %d != expected %d; falling back",
                        arr.shape[0], self._backend.dim,
                    )
                    return await self._embed_fallback(text)
                return arr
            except Exception as exc:
                log.warning("primary embed call failed (%s); using fallback", exc)
                return await self._embed_fallback(text)
        return await self._embed_fallback(text)

    async def _embed_fallback(self, text: str) -> np.ndarray | None:
        if self._st_model is None:
            try:
                await asyncio.to_thread(self._load_fallback)
            except Exception as exc:
                log.error("fallback embedding load failed: %s", exc)
                return None
        try:
            arr = await asyncio.to_thread(
                self._st_model.encode,
                [text],
                convert_to_numpy=True,
            )
            v = np.asarray(arr[0], dtype=np.float32)
            if v.shape[0] != self._backend.dim:
                log.error("fallback embedding dim mismatch: %d vs %d",
                          v.shape[0], self._backend.dim)
                return None
            return v
        except Exception as exc:
            log.error("fallback embed call failed: %s", exc)
            return None