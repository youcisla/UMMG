"""Directive 1 offline verification — no network, no real key.

Covers:
  * registry.resolve_adapter routes glm-* -> zai (and existing prefixes intact)
  * adapters.build_adapter constructs a ZaiAdapter
  * ZaiAdapter.chat() POSTs OpenAI-shape to {base}/chat/completions with
    a Bearer header and returns the upstream JSON unchanged
  * ZaiAdapter surfaces >=400 as httpx.HTTPStatusError (router passthrough)
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from registry import ModelRegistry, UnknownModelError
from adapters import build_adapter, ZaiAdapter
from adapters.zai import ZaiAdapter as DirectZai


def _registry() -> ModelRegistry:
    # Mirrors the current (pre-Directive-2) models.yaml namespace.
    return ModelRegistry(
        {"claude-opus": "anthropic", "minimax-m3": "minimax", "local-gemma4": "local"},
        {"anthropic": "http://127.0.0.1:8791"},
    )


def test_glm_prefix_routes_to_zai() -> None:
    r = _registry()
    assert r.resolve_adapter("glm-5.2") == "zai"
    assert r.resolve_adapter("glm-4.7") == "zai"


def test_existing_prefixes_unbroken() -> None:
    r = _registry()
    assert r.resolve_adapter("claude-sonnet") == "anthropic"
    assert r.resolve_adapter("minimax-m3") == "minimax"
    assert r.resolve_adapter("local-gemma4") == "local"
    with pytest.raises(UnknownModelError):
        r.resolve_adapter("totally-unknown")


def test_build_adapter_returns_zai() -> None:
    a = build_adapter("zai", "https://api.z.ai/api/coding/paas/v4", "test-key")
    assert isinstance(a, (ZaiAdapter, DirectZai))
    assert a.name == "zai"


def test_zai_chat_shape_and_auth() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-zai-1",
                "object": "chat.completion",
                "model": "glm-5.2",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    transport = httpx.MockTransport(handler)
    adapter = DirectZai("https://api.z.ai/api/coding/paas/v4", api_key="secret-key")

    async def run() -> dict:
        # Inject the mock transport into the adapter's client.
        import adapters.zai as zmod
        real_client = httpx.AsyncClient

        def _client(*args, **kwargs):  # noqa: ANN001, ANN002
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        zmod.httpx.AsyncClient = _client  # type: ignore[assignment]
        try:
            return await adapter.chat(
                {"model": "glm-5.2",
                 "messages": [{"role": "user", "content": "ping"}]}
            )
        finally:
            zmod.httpx.AsyncClient = real_client  # type: ignore[assignment]

    resp = asyncio.run(run())
    assert resp["choices"][0]["message"]["content"] == "pong"
    assert captured["url"].endswith("/api/coding/paas/v4/chat/completions")
    assert captured["auth"] == "Bearer secret-key"


def test_zai_surfaces_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    transport = httpx.MockTransport(handler)
    adapter = DirectZai("https://api.z.ai/api/coding/paas/v4", api_key="nope")

    async def run() -> None:
        import adapters.zai as zmod
        real_client = httpx.AsyncClient

        def _client(*args, **kwargs):  # noqa: ANN001, ANN002
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        zmod.httpx.AsyncClient = _client  # type: ignore[assignment]
        try:
            await adapter.chat({"model": "glm-4.7",
                                "messages": [{"role": "user", "content": "x"}]})
        finally:
            zmod.httpx.AsyncClient = real_client  # type: ignore[assignment]

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())
