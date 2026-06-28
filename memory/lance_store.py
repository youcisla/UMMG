"""LanceDB vector store.

Drop-in replacement for the FAISS ``VectorStore`` (memory/store.py). Exposes
the same surface — ``init() / add() / search() / size()`` — so memory/core.py
can switch backends by config alone.

Why LanceDB: it stores vectors + payload columns together in a columnar,
disk-backed Lance dataset (Apache Arrow), and runs approximate nearest-neighbour
search (IVF_PQ) directly off disk. That removes the FAISS constraint of holding
the whole index in RAM, which is the stated scaling risk for UMMG's perpetual
project history.

Semantics kept identical to the FAISS store:
  * vectors are queried by cosine similarity
  * search() returns payload dicts with a ``_score`` cosine-similarity field
    (LanceDB reports cosine *distance*; score = 1.0 - distance)
  * a dim mismatch against an existing table raises, rather than silently
    corrupting results

This module imports lancedb/pyarrow lazily so the gateway still boots with the
FAISS backend even when LanceDB is not installed.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("ummg.memory.lance")

# Payload columns persisted alongside the vector. Mirrors the dicts the FAISS
# store received via add(); unknown keys in a payload are ignored, missing keys
# default to empty/None.
_PAYLOAD_FIELDS: tuple[str, ...] = ("text", "role", "ts", "session_id", "model", "adapter")


class LanceVectorStore:
    """Columnar, disk-backed vector store with the FAISS store's interface."""

    def __init__(
        self,
        db_path: Path,
        table: str,
        dim: int,
        *,
        index_threshold: int = 512,
    ) -> None:
        self.db_path = Path(db_path)
        self.table_name = table
        self.dim = dim
        self.index_threshold = index_threshold
        self._lock = threading.RLock()
        self._db: Any = None
        self._tbl: Any = None
        self._indexed = False

    # ---- lifecycle -------------------------------------------------------
    def init(self) -> None:
        """Open (or create) the Lance table. Validates vector dimension."""
        import lancedb  # lazy: only required when this backend is selected
        import pyarrow as pa

        with self._lock:
            self.db_path.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(self.db_path))

            try:
                existing_tables = list(self._db.list_tables())
            except AttributeError:  # older lancedb
                existing_tables = list(self._db.table_names())
            if self.table_name in existing_tables:
                self._tbl = self._db.open_table(self.table_name)
                existing_dim = self._table_vector_dim(self._tbl)
                if existing_dim is not None and existing_dim != self.dim:
                    raise ValueError(
                        f"LanceDB dim mismatch: table={existing_dim}, expected={self.dim}"
                    )
            else:
                schema = pa.schema(
                    [
                        pa.field("vector", pa.list_(pa.float32(), self.dim)),
                        pa.field("text", pa.string()),
                        pa.field("role", pa.string()),
                        pa.field("ts", pa.float64()),
                        pa.field("session_id", pa.string()),
                        pa.field("model", pa.string()),
                        pa.field("adapter", pa.string()),
                    ]
                )
                self._tbl = self._db.create_table(self.table_name, schema=schema)
            # An existing, sufficiently large table may already carry an index.
            self._indexed = self.size() >= self.index_threshold

    @staticmethod
    def _table_vector_dim(tbl: Any) -> int | None:
        try:
            field = tbl.schema.field("vector")
            # fixed_size_list carries its width on the type.
            return int(getattr(field.type, "list_size", None) or 0) or None
        except Exception:  # noqa: BLE001 - schema introspection is best-effort
            return None

    # ---- writes ----------------------------------------------------------
    def add(self, vector: np.ndarray, payload: dict[str, Any]) -> None:
        with self._lock:
            if self._tbl is None:
                raise RuntimeError("LanceVectorStore.add() called before init()")
            v = np.asarray(vector, dtype=np.float32).reshape(-1)
            if v.shape[0] != self.dim:
                raise ValueError(
                    f"vector dim {v.shape[0]} != store dim {self.dim}"
                )
            row = {"vector": v.tolist()}
            for key in _PAYLOAD_FIELDS:
                val = payload.get(key)
                if key == "ts":
                    row[key] = float(val) if val is not None else 0.0
                else:
                    row[key] = "" if val is None else str(val)
            self._tbl.add([row])
            self._maybe_build_index()

    def _maybe_build_index(self) -> None:
        """Build an ANN index once the table is large enough to train one.

        Failures are non-fatal: an unindexed table still answers queries via a
        flat scan, so a training error must never break the write path.
        """
        if self._indexed:
            return
        try:
            n = self.size()
        except Exception:  # noqa: BLE001
            return
        if n < self.index_threshold:
            return
        try:
            self._tbl.create_index(metric="cosine", vector_column_name="vector")
            self._indexed = True
            log.info("LanceDB IVF index built on '%s' (%d rows)", self.table_name, n)
        except Exception as exc:  # noqa: BLE001
            log.warning("LanceDB index build skipped (%s); using flat scan", exc)

    # ---- reads -----------------------------------------------------------
    def search(self, vector: np.ndarray, top_k: int = 6) -> list[dict[str, Any]]:
        with self._lock:
            if self._tbl is None:
                raise RuntimeError("LanceVectorStore.search() called before init()")
            if self.size() == 0:
                return []
            v = np.asarray(vector, dtype=np.float32).reshape(-1)
            k = min(top_k, self.size())
            rows = (
                self._tbl.search(v)
                .metric("cosine")
                .limit(k)
                .to_list()
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            r.pop("vector", None)
            distance = r.pop("_distance", None)
            payload = {k2: v2 for k2, v2 in r.items() if not k2.startswith("_")}
            if distance is not None:
                # cosine distance -> cosine similarity, matching the FAISS store
                payload["_score"] = float(1.0 - distance)
            out.append(payload)
        return out

    def size(self) -> int:
        with self._lock:
            if self._tbl is None:
                return 0
            try:
                return int(self._tbl.count_rows())
            except Exception:  # noqa: BLE001
                return int(len(self._tbl))
