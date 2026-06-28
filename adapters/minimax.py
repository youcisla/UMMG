"""MiniMax adapter. Forwards OpenAI Chat Completions to headroom's
openai backend (default 8792). Headroom then forwards to the configured
OPENAI_TARGET_API_URL (api.minimax.io).
"""
from __future__ import annotations

from typing import Any, AsyncIterator

import httpx


class MinimaxAdapter:
    name = "minimax"

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def chat(self, payload: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def stream(self, payload: dict[str, Any], *, timeout: float = 120.0) -> AsyncIterator[bytes]:
        payload = {**payload, "stream": True}
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    yield chunk