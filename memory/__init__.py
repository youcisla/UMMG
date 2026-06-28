"""Memory core package.

Public surface:
  MemoryCore        - facade that wires events + store + embed + retrieve + summarize
  EventLog          - SQLite event log
  VectorStore       - FAISS index wrapper
  Embedder          - OpenAI-compat primary, sentence-transformers fallback
  Retriever         - top-K search via FAISS
  SummarizerWorker  - background rolling-summarizer task

Memory invariant: every model call writes to EventLog BEFORE and AFTER,
and reads via Retriever BEFORE.
"""
from __future__ import annotations

from .core import MemoryCore
from .events import EventLog
from .store import VectorStore
from .embed import Embedder
from .retrieve import Retriever
from .summarize import SummarizerWorker

__all__ = [
    "MemoryCore",
    "EventLog",
    "VectorStore",
    "Embedder",
    "Retriever",
    "SummarizerWorker",
]