"""Router. Exposes:
  POST /v1/chat/completions   - the only chat endpoint
  GET  /v1/models             - OpenAI-style model list
  GET  /health                - upstreams + memory stats

Memory injection pipeline (mandatory, both directions):
  pre_routing  -> embed + retrieve + summary -> build packet -> prepend
  post_routing -> log assistant + embed + upsert
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from adapters import build_adapter
from auth import require_bearer
from config import Settings
from context import ContextManager
from memory import MemoryCore
from observability import LatencyTracker, RequestRecord
from registry import ModelRegistry, UnknownModelError
from tracelog import TraceRecorder

log = logging.getLogger("ummg.router")


def make_router(
    *,
    settings: Settings,
    registry: ModelRegistry,
    memory: MemoryCore,
    tracker: LatencyTracker,
    recorder: TraceRecorder | None = None,
) -> APIRouter:
    router = APIRouter()
    ctx = ContextManager(max_context_tokens=settings.memory.max_context_tokens)

    # Pre-build adapters.
    adapters: dict[str, Any] = {}
    for adapter_name in sorted({a for a in settings.upstream.keys()}):
        if adapter_name == "anthropic":
            adapters[adapter_name] = build_adapter(adapter_name, settings.upstream[adapter_name], settings.anthropic_api_key)
        elif adapter_name == "minimax":
            adapters[adapter_name] = build_adapter(adapter_name, settings.upstream[adapter_name], settings.minimax_api_key)
        elif adapter_name == "local":
            adapters[adapter_name] = build_adapter(adapter_name, settings.upstream[adapter_name])
        elif adapter_name == "zai":
            if not settings.zai_api_key:
                log.warning("upstream 'zai' configured but ZAI_API_KEY missing; skipping zai adapter")
                continue
            adapters[adapter_name] = build_adapter(adapter_name, settings.upstream[adapter_name], settings.zai_api_key)
        else:
            log.warning("upstream '%s' has no adapter implementation; skipped", adapter_name)

    def _last_user_text(payload: dict[str, Any]) -> str:
        msgs = payload.get("messages") or []
        for m in reversed(msgs):
            if m.get("role") == "user":
                return m.get("content") or ""
        return ""

    def _strip_to_text(content: Any) -> str:
        # OpenAI content can be a string OR a list of parts.
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            out: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text" and part.get("text"):
                        out.append(part["text"])
                elif isinstance(part, str):
                    out.append(part)
            return "\n".join(out)
        return str(content or "")

    @router.get("/health")
    async def health() -> dict[str, Any]:
        # Fast TCP-level reachability probe per upstream. We deliberately
        # avoid full HTTP requests here so /health stays snappy even when
        # an upstream is hung or returning garbage.
        import asyncio

        async def probe(name: str, base: str) -> tuple[str, dict[str, Any]]:
            url = base.rstrip("/")
            # parse host:port
            from urllib.parse import urlparse
            p = urlparse(url if "://" in url else f"http://{url}")
            host = p.hostname or "127.0.0.1"
            port = p.port or (443 if p.scheme == "https" else 80)
            try:
                fut = asyncio.open_connection(host, port)
                reader, writer = await asyncio.wait_for(fut, timeout=1.5)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return name, {"url": base, "ok": True, "error": None}
            except Exception as exc:
                return name, {"url": base, "ok": False, "error": f"{type(exc).__name__}: {exc}"}

        results = await asyncio.gather(*(probe(n, b) for n, b in settings.upstream.items()))
        upstream_status = dict(results)
        return {
            "ok": all(s["ok"] for s in upstream_status.values()),
            "upstreams": upstream_status,
            "memory": memory.stats(),
            "latency": tracker.summary(),
        }

    @router.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        models = registry.list_models()
        return {
            "object": "list",
            "data": [
                {
                    "id": m,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "ummg",
                }
                for m in models
            ],
        }

    @router.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        _bearer: str = Depends(require_bearer),
    ) -> Any:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Body must be a JSON object")
        model = payload.get("model")
        if not model or not isinstance(model, str):
            raise HTTPException(status_code=400, detail="Missing or invalid 'model' field")

        try:
            adapter_name = registry.resolve_adapter(model)
        except UnknownModelError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        adapter = adapters.get(adapter_name)
        if adapter is None:
            raise HTTPException(
                status_code=503,
                detail=f"Adapter '{adapter_name}' not configured (missing upstream)",
            )

        # -------- per-model registry overrides (Directive 2) --------
        spec = registry.spec_for(model)
        if spec is not None:
            if getattr(spec, "native_model", None):
                # Forward the provider's exact id while keeping `model` (the
                # friendly name) for memory + metrics.
                payload = {**payload, "model": spec.native_model}
            if getattr(spec, "default_max_tokens", None) and "max_tokens" not in payload:
                # Reasoning models exhaust short budgets on hidden reasoning
                # tokens; supply headroom only when the client didn't specify.
                payload = {**payload, "max_tokens": spec.default_max_tokens}

        # Session id: header x-ummg-session-id > payload.session_id > uuid4
        session_id = (
            request.headers.get("x-ummg-session-id")
            or payload.get("session_id")
            or str(uuid.uuid4())
        )

        user_text = _strip_to_text(_last_user_text(payload))
        if not user_text:
            raise HTTPException(
                status_code=400,
                detail="Last message must have a non-empty 'user' role with text content",
            )

        # -------- memory injection (BEFORE) --------
        t0 = time.perf_counter()
        memories: list[dict[str, Any]] = []
        latest_summary: dict[str, Any] | None = None
        if settings.memory.enabled:
            try:
                memories, latest_summary = await memory.pre_routing(
                    model=model, session_id=session_id, user_text=user_text
                )
            except Exception:
                log.exception("memory pre_routing failed; serving without injection")

        # Build packet + truncate
        packet = ctx.build_packet(memories, latest_summary) if settings.memory.enabled else None
        messages = list(payload.get("messages") or [])
        if packet:
            # Insert packet as the first system message.
            # Strip any client-supplied system messages to keep one canonical packet.
            messages = [m for m in messages if m.get("role") != "system"]
            messages = [packet] + messages
        augmented = {**payload, "messages": messages}
        effective_ctx = settings.memory.max_context_tokens
        if spec is not None and getattr(spec, "context_window", None):
            # Never inject/keep more than the model can actually accept.
            effective_ctx = min(effective_ctx, spec.context_window)
        augmented = ctx.truncate(augmented, max_context_tokens=effective_ctx)

        # -------- adapter call --------
        is_stream = bool(augmented.get("stream", False))
        upstream_latency = 0.0
        assistant_text = ""
        tokens_in: int | None = None
        tokens_out: int | None = None
        http_status = 200

        try:
            if is_stream:
                # For streaming, we buffer just enough to capture the final
                # assistant text for the memory write. We still pass bytes
                # straight through to the client.
                async def stream_and_collect():
                    nonlocal assistant_text, http_status, upstream_latency
                    chunks_buf: list[str] = []
                    try:
                        async for chunk in adapter.stream(augmented):
                            yield chunk
                            # Try to extract delta text for memory write.
                            try:
                                text = chunk.decode("utf-8", errors="ignore")
                            except Exception:
                                continue
                            for line in text.splitlines():
                                if line.startswith("data: "):
                                    payload_str = line[len("data: "):].strip()
                                    if not payload_str or payload_str == "[DONE]":
                                        continue
                                    try:
                                        import json
                                        d = json.loads(payload_str)
                                        delta = d.get("choices", [{}])[0].get("delta", {})
                                        c = delta.get("content")
                                        if isinstance(c, str):
                                            chunks_buf.append(c)
                                    except Exception:
                                        pass
                    except httpx.HTTPStatusError as exc:
                        http_status = exc.response.status_code
                        raise
                    assistant_text = "".join(chunks_buf)
                    upstream_latency = (time.perf_counter() - t0) * 1000.0

                async def streamer():
                    try:
                        async for chunk in stream_and_collect():
                            yield chunk
                    finally:
                        # After stream completes, write to memory + record metrics.
                        if assistant_text:
                            await memory.post_routing(
                                model=model,
                                adapter=adapter.name,
                                session_id=session_id,
                                assistant_text=assistant_text,
                                latency_ms=upstream_latency,
                                tokens_in=None,
                                tokens_out=None,
                            )
                        if recorder is not None:
                            recorder.record({
                                "type": "chat.completion.stream",
                                "session_id": session_id,
                                "model": model,
                                "adapter": adapter.name,
                                "request": {
                                    "messages": payload.get("messages"),
                                    "max_tokens": payload.get("max_tokens"),
                                    "temperature": payload.get("temperature"),
                                },
                                "response": {"content": assistant_text},
                                "latency_ms": round(upstream_latency, 2),
                            })
                        latency_ms = (time.perf_counter() - t0) * 1000.0
                        tracker.record(RequestRecord(
                            ts=time.time(),
                            model=model,
                            adapter=adapter.name,
                            status=http_status,
                            latency_ms=latency_ms,
                            tokens_in=None,
                            tokens_out=None,
                            memories_used=len(memories),
                        ))
                        log.info(
                            "request_done",
                            extra={
                                "model": model, "adapter": adapter.name,
                                "status": http_status, "latency_ms": round(latency_ms, 2),
                                "memories_used": len(memories),
                                "stream": True,
                            },
                        )

                return StreamingResponse(
                    streamer(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            else:
                # Non-streaming chat
                response_json = await adapter.chat(augmented)
                upstream_latency = (time.perf_counter() - t0) * 1000.0
                # Extract assistant text + token usage if present.
                try:
                    assistant_text = _strip_to_text(
                        response_json["choices"][0]["message"]["content"]
                    )
                except Exception:
                    assistant_text = ""
                usage = response_json.get("usage") or {}
                tokens_in = usage.get("prompt_tokens")
                tokens_out = usage.get("completion_tokens")
        except httpx.HTTPStatusError as exc:
            http_status = exc.response.status_code
            try:
                err_body = exc.response.json()
            except Exception:
                err_body = {"detail": exc.response.text[:500]}
            return JSONResponse(status_code=http_status, content=err_body)
        except httpx.RequestError as exc:
            log.warning("upstream request error: %s", exc)
            return JSONResponse(
                status_code=502,
                content={"error": {"type": "upstream_unreachable", "message": str(exc),
                                   "adapter": adapter.name, "upstream": settings.upstream.get(adapter.name)}},
            )

        # -------- memory write (AFTER) --------
        if assistant_text:
            try:
                await memory.post_routing(
                    model=model,
                    adapter=adapter.name,
                    session_id=session_id,
                    assistant_text=assistant_text,
                    latency_ms=upstream_latency,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )
            except Exception:
                log.exception("memory post_routing failed")

        if recorder is not None:
            recorder.record({
                "type": "chat.completion",
                "session_id": session_id,
                "model": model,
                "adapter": adapter.name,
                "request": {
                    "messages": payload.get("messages"),
                    "max_tokens": payload.get("max_tokens"),
                    "temperature": payload.get("temperature"),
                },
                "response": {
                    "content": assistant_text,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                },
                "latency_ms": round(upstream_latency, 2),
            })

        latency_ms = (time.perf_counter() - t0) * 1000.0
        tracker.record(RequestRecord(
            ts=time.time(),
            model=model,
            adapter=adapter.name,
            status=http_status,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            memories_used=len(memories),
        ))
        log.info(
            "request_done",
            extra={
                "model": model, "adapter": adapter.name,
                "status": http_status, "latency_ms": round(latency_ms, 2),
                "memories_used": len(memories),
                "stream": False,
            },
        )
        return response_json

    return router