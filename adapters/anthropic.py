"""Anthropic adapter. Translates OpenAI Chat Completions shape to
Anthropic Messages API and POSTs to /v1/messages on headroom 8791.

Headroom's anthropic backend serves /v1/messages -> api.anthropic.com.
Headroom does NOT serve /v1/chat/completions -> Anthropic (that's
OpenAI's shape and headroom routes it to OpenAI regardless of --backend).

So this adapter owns the OpenAI -> Anthropic shape translation.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx


# Map of common OpenAI model id substrings -> Anthropic native model id.
# Falls back to whatever the client passed if no match.
_MODEL_ALIASES = {
    "claude-opus": "claude-opus-4-1",
    "claude-sonnet": "claude-sonnet-4-5",
    "claude-haiku": "claude-haiku-4-5",
}


def _resolve_native_model(name: str) -> str:
    for prefix, native in _MODEL_ALIASES.items():
        if name.startswith(prefix):
            return native
    return name


def _flatten_content(content: Any) -> str | list[dict[str, Any]]:
    """Convert OpenAI content (str or list of parts) to Anthropic content
    (str for simple text, or list of {type, text} blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and part.get("text"):
                    blocks.append({"type": "text", "text": part["text"]})
                elif "text" in part:
                    blocks.append({"type": "text", "text": str(part["text"])})
            elif isinstance(part, str):
                blocks.append({"type": "text", "text": part})
        if blocks:
            return blocks
    return str(content or "")


def openai_to_anthropic(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate OpenAI Chat Completions payload -> Anthropic Messages payload."""
    messages = payload.get("messages") or []
    system_parts: list[str] = []
    anthropic_msgs: list[dict[str, Any]] = []

    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            text = content if isinstance(content, str) else _flatten_content(content)
            if isinstance(text, str) and text:
                system_parts.append(text)
            continue
        if role in ("user", "assistant"):
            anthropic_msgs.append({
                "role": role,
                "content": _flatten_content(content),
            })
        # tool messages are folded into a user text block (best-effort)
        elif role == "tool":
            text = content if isinstance(content, str) else json.dumps(content)
            anthropic_msgs.append({"role": "user", "content": f"[tool result] {text}"})

    if not anthropic_msgs:
        raise ValueError("Anthropic Messages requires at least one user/assistant message")

    # Anthropic requires alternating user/assistant and first must be user.
    # Merge consecutive same-role messages.
    merged: list[dict[str, Any]] = []
    for m in anthropic_msgs:
        if merged and merged[-1]["role"] == m["role"]:
            prev = merged[-1]["content"]
            cur = m["content"]
            if isinstance(prev, str) and isinstance(cur, str):
                merged[-1]["content"] = prev + "\n\n" + cur
            else:
                merged[-1]["content"] = [
                    *([{"type": "text", "text": prev}] if isinstance(prev, str) else prev),
                    *([{"type": "text", "text": cur}] if isinstance(cur, str) else cur),
                ]
        else:
            merged.append(dict(m))
    if merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "(continuation)"})

    out: dict[str, Any] = {
        "model": _resolve_native_model(payload.get("model", "")),
        "messages": merged,
        "max_tokens": int(payload.get("max_tokens", 1024)),
    }
    if system_parts:
        out["system"] = "\n\n".join(system_parts)
    if "temperature" in payload:
        out["temperature"] = float(payload["temperature"])
    if "top_p" in payload:
        out["top_p"] = float(payload["top_p"])
    if "stop" in payload:
        out["stop_sequences"] = payload["stop"] if isinstance(payload["stop"], list) else [payload["stop"]]
    return out


def anthropic_to_openai(
    anthropic_resp: dict[str, Any],
    requested_model: str,
) -> dict[str, Any]:
    """Translate Anthropic Messages response -> OpenAI Chat Completions response."""
    content_blocks = anthropic_resp.get("content") or []
    text_parts: list[str] = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif isinstance(block, str):
            text_parts.append(block)
    text = "".join(text_parts)
    usage = anthropic_resp.get("usage") or {}
    return {
        "id": anthropic_resp.get("id", "chatcmpl-anthropic"),
        "object": "chat.completion",
        "created": int(__import__("time").time()),
        "model": requested_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": _map_stop_reason(anthropic_resp.get("stop_reason")),
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": (usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0),
        },
    }


def _map_stop_reason(reason: Any) -> str:
    if reason in ("end_turn", "stop_sequence"):
        return "stop"
    if reason == "max_tokens":
        return "length"
    if reason == "tool_use":
        return "tool_calls"
    return "stop"


class AnthropicAdapter:
    name = "anthropic"

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # OAuth (Pro subscription) tokens start with sk-ant-oat01- and use
        # Authorization: Bearer. API keys start with sk-ant-api03- and use
        # x-api-key. The Anthropic API rejects mismatched headers, so pick
        # the correct one based on the prefix.
        self._use_oauth = bool(api_key) and api_key.startswith("sk-ant-oat")

    def _headers(self, stream: bool = False) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            # Anthropic Messages API requires this version header on every
            # call — easy to forget when forwarding through headroom.
            "anthropic-version": "2023-06-01",
        }
        if self.api_key:
            if self._use_oauth:
                # Pro / OAuth: Anthropic expects Authorization: Bearer <token>.
                # The token (sk-ant-oat01-…) is what the Claude Code CLI sends
                # when it's running under a Pro/AI-subscription login.
                h["Authorization"] = f"Bearer {self.api_key}"
            else:
                # API key: traditional x-api-key header.
                h["x-api-key"] = self.api_key
        if stream:
            h["Accept"] = "text/event-stream"
        return h

    async def chat(self, payload: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
        anthropic_body = openai_to_anthropic(payload)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{self.base_url}/v1/messages",
                json=anthropic_body,
                headers=self._headers(),
            )
            if r.status_code >= 400:
                # Surface upstream error verbatim.
                raise httpx.HTTPStatusError(
                    f"{r.status_code} {r.reason_phrase}",
                    request=r.request,
                    response=r,
                )
            return anthropic_to_openai(r.json(), requested_model=payload.get("model", ""))

    async def stream(self, payload: dict[str, Any], *, timeout: float = 120.0) -> AsyncIterator[bytes]:
        anthropic_body = openai_to_anthropic(payload)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/messages",
                json=anthropic_body,
                headers=self._headers(stream=True),
            ) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    yield chunk