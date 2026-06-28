"""Adapter protocol. All adapters expose chat() and stream()."""
from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class Adapter(Protocol):
    name: str

    async def chat(self, payload: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
        ...

    async def stream(self, payload: dict[str, Any], *, timeout: float = 120.0) -> AsyncIterator[bytes]:
        ...