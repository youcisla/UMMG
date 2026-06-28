"""FAISS vector store wrapper.

Persists a flat inner-product index plus a parallel payload store
(JSON list, one entry per vector). For v1 this is sufficient for
single-user localhost scale (~10k events fine). When the index gets
large enough that IndexFlatIP slows down, swap in IndexIVFFlat without
changing the API.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import faiss
import numpy as np


class VectorStore:
    def __init__(self, index_path: Path, payloads_path: Path, dim: int) -> None:
        self.index_path = index_path
        self.payloads_path = payloads_path
        self.dim = dim
        self._lock = threading.RLock()
        self._index: faiss.Index | None = None
        self._payloads: list[dict[str, Any]] = []

    def init(self) -> None:
        with self._lock:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            if self.index_path.exists() and self.payloads_path.exists():
                try:
                    self._index = faiss.read_index(str(self.index_path))
                    with self.payloads_path.open("r", encoding="utf-8") as fh:
                        self._payloads = json.load(fh)
                    if self._index.d != self.dim:
                        raise ValueError(
                            f"FAISS dim mismatch: index={self._index.d}, expected={self.dim}"
                        )
                    return
                except Exception:
                    # Corrupt store -> recreate empty
                    self._index = None
                    self._payloads = []
            self._index = faiss.IndexFlatIP(self.dim)
            self._payloads = []
            self._persist()

    def _persist(self) -> None:
        assert self._index is not None
        faiss.write_index(self._index, str(self.index_path))
        with self.payloads_path.open("w", encoding="utf-8") as fh:
            json.dump(self._payloads, fh, ensure_ascii=False)

    def size(self) -> int:
        with self._lock:
            assert self._index is not None
            return int(self._index.ntotal)

    def add(self, vector: np.ndarray, payload: dict[str, Any]) -> None:
        with self._lock:
            assert self._index is not None
            v = np.asarray(vector, dtype=np.float32).reshape(1, -1)
            # L2-normalize so inner product == cosine similarity.
            faiss.normalize_L2(v)
            self._index.add(v)
            self._payloads.append(payload)
            # Persist every write; small cost, large reliability win for v1.
            self._persist()

    def search(self, vector: np.ndarray, top_k: int = 6) -> list[dict[str, Any]]:
        with self._lock:
            assert self._index is not None
            n = int(self._index.ntotal)
            if n == 0:
                return []
            k = min(top_k, n)
            v = np.asarray(vector, dtype=np.float32).reshape(1, -1)
            faiss.normalize_L2(v)
            scores, ids = self._index.search(v, k)
            out: list[dict[str, Any]] = []
            for score, idx in zip(scores[0].tolist(), ids[0].tolist()):
                if idx < 0 or idx >= len(self._payloads):
                    continue
                p = dict(self._payloads[idx])
                p["_score"] = float(score)
                out.append(p)
            return out