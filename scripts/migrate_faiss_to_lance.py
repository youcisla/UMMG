"""One-shot migration: FAISS flat index -> LanceDB table.

Reconstructs the stored vectors directly from the FAISS IndexFlatIP (no
re-embedding) and pairs them with the existing payloads, then writes them into
the LanceDB table the gateway will read when `vector_backend: lancedb`.

Run on the Windows host from the repo root, with the gateway stopped:

    python scripts/migrate_faiss_to_lance.py
    python scripts/migrate_faiss_to_lance.py --dry-run

Idempotency: refuses to run if the target table already has rows unless
--force is given. Safe to re-run after deleting the lance/ dir.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import load_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate FAISS memory -> LanceDB")
    ap.add_argument("--dry-run", action="store_true", help="report counts, write nothing")
    ap.add_argument("--force", action="store_true", help="append even if table non-empty")
    args = ap.parse_args()

    settings = load_settings()
    data_dir = settings.data_dir
    index_path = data_dir / "vectors.faiss"
    payloads_path = data_dir / "vectors.payloads.json"

    if not index_path.exists() or not payloads_path.exists():
        print(f"FAIL: no FAISS store found at {data_dir} (nothing to migrate)", file=sys.stderr)
        return 2

    try:
        import faiss  # type: ignore
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: faiss/numpy required to read the source index: {exc}", file=sys.stderr)
        return 2

    index = faiss.read_index(str(index_path))
    payloads = json.loads(payloads_path.read_text(encoding="utf-8"))
    n = int(index.ntotal)
    if n != len(payloads):
        print(f"WARN: index has {n} vectors but {len(payloads)} payloads; "
              f"migrating min({n},{len(payloads)})", file=sys.stderr)
    n = min(n, len(payloads))
    dim = int(index.d)
    print(f"source: {n} vectors, dim={dim}")

    if args.dry_run:
        print("dry-run: no writes performed")
        return 0

    from memory.lance_store import LanceVectorStore

    store = LanceVectorStore(
        db_path=data_dir / "lance",
        table=settings.memory.lance_table,
        dim=dim,
        index_threshold=settings.memory.lance_index_threshold,
    )
    store.init()
    if store.size() > 0 and not args.force:
        print(f"FAIL: target table already has {store.size()} rows; pass --force to append",
              file=sys.stderr)
        return 1

    migrated = 0
    for i in range(n):
        vec = index.reconstruct(i)  # exact for IndexFlatIP
        store.add(np.asarray(vec, dtype="float32"), payloads[i])
        migrated += 1
        if migrated % 500 == 0:
            print(f"  migrated {migrated}/{n}")

    print(f"OK: migrated {migrated} rows into LanceDB table "
          f"'{settings.memory.lance_table}' (size now {store.size()})")
    print("Next: set memory.vector_backend: lancedb in models.yaml and restart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
