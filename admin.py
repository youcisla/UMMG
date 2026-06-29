"""Admin / dashboard API + static UI.

Read-only operational surface for the UMMG dashboard. All JSON endpoints sit
behind the same bearer auth as the chat endpoint; the HTML shell at ``/ui`` is
unauthenticated (it contains no secrets — the browser supplies the token at
runtime and calls the JSON endpoints with it).

Endpoints:
    GET  /ui                      -> dashboard HTML (no auth)
    GET  /v1/admin/overview       -> models, backend, memory + latency stats
    GET  /v1/admin/events?limit=  -> recent ledger events (text truncated)
    GET  /v1/admin/traces?limit=  -> tail of today's trace JSONL (if enabled)
    POST /v1/admin/memory/search  -> {query, top_k} -> retrieved memories
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from auth import require_bearer

log = logging.getLogger("ummg.admin")

_UI_PATH = Path(__file__).resolve().parent / "ui" / "index.html"
_TEXT_PREVIEW = 600


def _truncate(text: Any, limit: int = _TEXT_PREVIEW) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[:limit] + "…"


def make_admin_router(
    *,
    settings: Any,
    registry: Any,
    memory: Any,
    tracker: Any,
) -> APIRouter:
    router = APIRouter()

    @router.get("/ui")
    async def ui() -> FileResponse:
        if not _UI_PATH.exists():
            raise HTTPException(status_code=404, detail="dashboard not installed")
        return FileResponse(str(_UI_PATH), media_type="text/html")

    @router.get("/v1/admin/dev_token")
    async def dev_token(request: Request) -> dict[str, str]:
        """Returns the configured bearer token for callers on localhost.

        Used by the bundled dashboard to auto-fill its token field instead
        of forcing the user to copy/paste from .env every reload. Refuses
        any caller whose socket is not on loopback — a remote attacker
        who already has reach to 127.0.0.1:8787 from your machine would
        already own the box, but this still keeps the token out of any
        forward proxy or accidental public exposure.

        Disable with UMMG_DISABLE_DEV_TOKEN=1 in the environment.
        """
        import os
        if os.getenv("UMMG_DISABLE_DEV_TOKEN", "").strip().lower() in ("1", "true", "yes"):
            raise HTTPException(status_code=404, detail="disabled")
        client = request.client
        host = (client.host if client else "") or ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(status_code=403, detail="loopback only")
        return {"token": settings.bearer_token}

    @router.get("/v1/admin/overview")
    async def overview(_bearer: str = Depends(require_bearer)) -> dict[str, Any]:
        try:
            events_total = await memory.events.count()
        except Exception:  # noqa: BLE001
            events_total = None
        try:
            summary = await memory.events.last_summary()
        except Exception:  # noqa: BLE001
            summary = None
        return {
            "models": registry.list_models(),
            "upstreams": list(settings.upstream.keys()),
            "vector_backend": settings.memory.vector_backend,
            "tracing_enabled": settings.tracing.enabled,
            "memory": memory.stats(),
            "events_total": events_total,
            "has_summary": bool(summary),
            "latency": tracker.summary(),
        }

    @router.get("/v1/admin/events")
    async def events(limit: int = 50, _bearer: str = Depends(require_bearer)) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        try:
            rows = await memory.events.recent(limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"events read failed: {exc}")
        for r in rows:
            if "text" in r:
                r["text"] = _truncate(r.get("text"))
        return {"events": rows}

    @router.get("/v1/admin/traces")
    async def traces(limit: int = 50, _bearer: str = Depends(require_bearer)) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        if not settings.tracing.enabled:
            return {"enabled": False, "traces": []}
        trace_dir = settings.data_dir / settings.tracing.dir
        files = sorted(trace_dir.glob("traces-*.jsonl")) if trace_dir.exists() else []
        if not files:
            return {"enabled": True, "traces": []}
        latest = files[-1]
        try:
            lines = latest.read_text(encoding="utf-8").splitlines()[-limit:]
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"trace read failed: {exc}")
        out: list[dict[str, Any]] = []
        for ln in reversed(lines):
            try:
                rec = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            resp = rec.get("response") or {}
            if isinstance(resp, dict) and "content" in resp:
                resp = {**resp, "content": _truncate(resp.get("content"), 300)}
            out.append({
                "ts": rec.get("ts"),
                "model": rec.get("model"),
                "adapter": rec.get("adapter"),
                "type": rec.get("type"),
                "latency_ms": rec.get("latency_ms"),
                "response": resp,
            })
        return {"enabled": True, "file": latest.name, "traces": out}

    @router.post("/v1/admin/memory/search")
    async def memory_search(
        payload: dict[str, Any] = Body(...),
        _bearer: str = Depends(require_bearer),
    ) -> dict[str, Any]:
        query = str(payload.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="missing 'query'")
        top_k = max(1, min(int(payload.get("top_k", 6)), 50))
        retriever = getattr(memory, "retriever", None)
        if retriever is None:
            return {"results": [], "note": "memory retriever not initialized"}
        try:
            results = await retriever.retrieve(query)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"search failed: {exc}")
        for r in results:
            if "text" in r:
                r["text"] = _truncate(r.get("text"))
        return {"results": results[:top_k]}

    return router
