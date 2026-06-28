"""UMMG gateway entrypoint.

Usage:
    python main.py            # serve on 127.0.0.1:8787
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from config import load_settings
from log import setup_logging, get_logger
from memory import MemoryCore
from observability import LatencyTracker
from registry import ModelRegistry
from router import make_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = get_logger()
    log.info("ummg starting")
    settings = app.state.settings
    registry = ModelRegistry(settings.models, settings.upstream)
    memory = MemoryCore(
        settings.memory,
        settings.data_dir,
        anthropic_upstream=settings.upstream.get("anthropic", "http://127.0.0.1:8791"),
        anthropic_api_key=settings.anthropic_api_key,
    )
    await memory.init()
    tracker = LatencyTracker()
    app.state.registry = registry
    app.state.memory = memory
    app.state.tracker = tracker

    app.include_router(make_router(
        settings=settings,
        registry=registry,
        memory=memory,
        tracker=tracker,
    ))

    log.info(
        "ummg ready on http://%s:%d  models=%s  upstreams=%s",
        settings.host, settings.port,
        registry.list_models(), list(settings.upstream.keys()),
    )
    try:
        yield
    finally:
        log.info("ummg shutting down")
        await memory.shutdown()


def create_app() -> FastAPI:
    setup_logging()
    settings = load_settings()
    app = FastAPI(
        title="UMMG — Unified Model Memory Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.bearer_token = settings.bearer_token
    return app


app = create_app()


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()