"""Directive 3 verification — LanceVectorStore parity & semantics.

Skips automatically if lancedb isn't installed (e.g. CI without the optional
dep). When present, validates retrieval ordering against a brute-force cosine
baseline plus score semantics and dim-mismatch guarding.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

lancedb = pytest.importorskip("lancedb")

# Load lance_store.py in isolation: importing the `memory` package would pull in
# faiss/aiosqlite (the FAISS backend + event ledger), which aren't needed here.
import importlib.util as _ilu  # noqa: E402

_LS_PATH = Path(__file__).resolve().parent.parent / "memory" / "lance_store.py"
_spec = _ilu.spec_from_file_location("lance_store_isolated", _LS_PATH)
assert _spec and _spec.loader
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
LanceVectorStore = _mod.LanceVectorStore


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def _new_store(tmp: Path, dim: int) -> LanceVectorStore:
    s = LanceVectorStore(db_path=tmp / "lance", table="memory", dim=dim, index_threshold=10_000)
    s.init()
    return s


def test_add_search_parity_vs_brute_force() -> None:
    rng = np.random.default_rng(42)
    dim = 64
    vecs = rng.standard_normal((50, dim)).astype("float32")
    with tempfile.TemporaryDirectory() as td:
        store = _new_store(Path(td), dim)
        for i, v in enumerate(vecs):
            store.add(v, {"text": f"doc {i}", "role": "user", "ts": float(i),
                          "session_id": "s", "model": "m", "adapter": "a"})
        assert store.size() == 50

        query = vecs[7] + 0.01 * rng.standard_normal(dim).astype("float32")
        got = store.search(query, top_k=5)
        assert got, "expected results"
        # Brute-force top-1 should match Lance top-1.
        sims = [(_cosine(query, v), i) for i, v in enumerate(vecs)]
        sims.sort(reverse=True)
        expected_top = f"doc {sims[0][1]}"
        assert got[0]["text"] == expected_top
        # Score is cosine similarity in [-1, 1], descending.
        scores = [r["_score"] for r in got]
        assert scores == sorted(scores, reverse=True)
        assert -1.001 <= scores[0] <= 1.001


def test_empty_store_returns_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = _new_store(Path(td), 16)
        assert store.size() == 0
        assert store.search(np.ones(16, dtype="float32")) == []


def test_dim_mismatch_on_add_raises() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = _new_store(Path(td), 8)
        with pytest.raises(ValueError):
            store.add(np.ones(9, dtype="float32"), {"text": "x"})
