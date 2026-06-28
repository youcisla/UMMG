"""Z.ai adapter.

Forwards OpenAI Chat Completions to the Z.ai GLM **Coding Plan** endpoint,
which is OpenAI-compatible:

    POST {base_url}/chat/completions
    Authorization: Bearer <ZAI_API_KEY>

`base_url` is expected to be the OpenAI-compatible coding base, i.e.
``https://api.z.ai/api/coding/paas/v4`` (note: the coding-plan path, NOT the
general ``/api/paas/v4`` path), or a local Headroom instance fronting it.
Because the path already ends in ``/paas/v4`` there is no extra ``/v1`` segment
(this mirrors LocalAdapter, not MinimaxAdapter).

Like all adapters this is a thin pass-through: no memory logic, no payload
reshaping beyond what the upstream requires. The model id (e.g. ``glm-5.2``,
``glm-4.7``) is forwarded verbatim from the client request.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

import httpx


class ZaiAdapter:
    name = "zai"

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self, stream: bool = False) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if stream:
            h["Accept"] = "text/event-stream"
        return h

    async def chat(self, payload: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            if r.status_code >= 400:
                # Surface the upstream error verbatim; the router turns this
                # HTTPStatusError into a passthrough JSONResponse with the body.
                raise httpx.HTTPStatusError(
                    f"{r.status_code} {r.reason_phrase}",
                    request=r.request,
                    response=r,
                )
            return r.json()

    async def stream(self, payload: dict[str, Any], *, timeout: float = 120.0) -> AsyncIterator[bytes]:
        body = {**payload, "stream": True}
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=body,
                headers=self._headers(stream=True),
            ) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    yield chunk
