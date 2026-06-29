"""UI/admin verification — auth gate + endpoint shapes, with stand-in deps."""
from __future__ import annotations

import types

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from admin import make_admin_router  # noqa: E402


def _settings():
    return types.SimpleNamespace(
        upstream={"anthropic": "x", "zai": "y", "minimax": "z"},
        memory=types.SimpleNamespace(vector_backend="faiss"),
        tracing=types.SimpleNamespace(enabled=False, dir="traces"),
        data_dir=__import__("pathlib").Path("/tmp/ummg-test-data"),
    )


class _Events:
    async def count(self): return 3
    async def last_summary(self): return {"text": "s"}
    async def recent(self, limit=50):
        return [
            {"ts": 1.0, "role": "user", "model": "glm-5.2", "adapter": None, "text": "x" * 5000},
            {"ts": 2.0, "role": "assistant", "model": "glm-5.2", "adapter": "zai", "text": "ok"},
        ][:limit]


class _Retriever:
    async def retrieve(self, q): return [{"role": "assistant", "text": "mem", "_score": 0.9, "model": "m"}]


class _Memory:
    def __init__(self):
        self.events = _Events()
        self.retriever = _Retriever()
    def stats(self): return {"store_size": 42, "data_dir": "/tmp"}


def _app():
    app = FastAPI()
    app.state.bearer_token = "tok"
    app.include_router(make_admin_router(
        settings=_settings(),
        registry=types.SimpleNamespace(list_models=lambda: ["glm-5.2", "claude-sonnet", "minimax-m3"]),
        memory=_Memory(),
        tracker=types.SimpleNamespace(summary=lambda: {"count": 2, "avg_latency_ms": 10.0, "p95_latency_ms": 12.0}),
    ))
    return TestClient(app)


def test_overview_requires_token() -> None:
    c = _app()
    assert c.get("/v1/admin/overview").status_code == 401
    r = c.get("/v1/admin/overview", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    body = r.json()
    assert body["vector_backend"] == "faiss"
    assert body["events_total"] == 3
    assert set(["glm-5.2", "claude-sonnet"]).issubset(body["models"])


def test_events_truncated() -> None:
    c = _app()
    r = c.get("/v1/admin/events?limit=5", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    evs = r.json()["events"]
    assert len(evs) == 2
    assert evs[0]["text"].endswith("…")  # long text truncated for UI


def test_memory_search() -> None:
    c = _app()
    r = c.post("/v1/admin/memory/search", headers={"Authorization": "Bearer tok"},
               json={"query": "auth design", "top_k": 5})
    assert r.status_code == 200
    res = r.json()["results"]
    assert res and res[0]["_score"] == 0.9
    # missing query -> 400
    assert c.post("/v1/admin/memory/search", headers={"Authorization": "Bearer tok"},
                  json={}).status_code == 400


def test_ui_served() -> None:
    c = _app()
    r = c.get("/ui")
    assert r.status_code == 200
    assert "UMMG" in r.text and "Gateway Console" in r.text
