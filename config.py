"""Configuration loading. Reads .env (if present) and models.yaml.

Exposes a single `Settings` object built once at startup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
MODELS_PATH = ROOT / "models.yaml"
DATA_DIR = Path(os.getenv("UMMG_DATA_DIR", str(ROOT / "data")))


@dataclass(frozen=True)
class ModelSpec:
    """Per-model registry entry.

    `adapter` is required. The rest are optional metadata the router uses to
    forward the request correctly and to size injected memory per model.
    """

    adapter: str
    native_model: str | None = None
    context_window: int | None = None
    default_max_tokens: int | None = None


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool
    top_k: int
    max_context_tokens: int
    summary_every_n_events: int
    summary_window: int
    summary_model: str
    embedding_primary_base_url: str
    embedding_primary_model: str
    embedding_fallback: str
    embedding_fallback_model: str
    vector_backend: str
    lance_table: str
    lance_index_threshold: int


@dataclass(frozen=True)
class TraceConfig:
    enabled: bool
    dir: str
    queue_max: int


@dataclass(frozen=True)
class Settings:
    bearer_token: str
    anthropic_api_key: str
    minimax_api_key: str
    zai_api_key: str
    host: str
    port: int
    upstream: dict[str, str]
    models: dict[str, ModelSpec]
    memory: MemoryConfig
    tracing: TraceConfig
    data_dir: Path


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val or val.startswith("change-me") or val.startswith("sk-..."):
        raise SystemExit(
            f"ERROR: environment variable {name} is missing or still set to placeholder. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


def _optional_env(name: str) -> str:
    """Like _require_env but returns '' when missing/placeholder instead of
    aborting. Used for providers that are optional unless wired in models.yaml."""
    val = os.getenv(name)
    if not val or val.startswith("change-me") or val.startswith("sk-..."):
        return ""
    return val


def _load_models_yaml() -> dict[str, Any]:
    with MODELS_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _parse_models(raw_models: dict[str, Any] | None) -> dict[str, ModelSpec]:
    """Parse the `models:` block into ModelSpec objects.

    Entries lacking an `adapter` are skipped (with no hard failure) so a
    malformed line can't take the whole gateway down at boot.
    """
    out: dict[str, ModelSpec] = {}
    for name, cfg in (raw_models or {}).items():
        if not isinstance(cfg, dict) or "adapter" not in cfg:
            continue
        nm = cfg.get("native_model")
        cw = cfg.get("context_window")
        dmt = cfg.get("default_max_tokens")
        out[str(name)] = ModelSpec(
            adapter=str(cfg["adapter"]),
            native_model=str(nm) if nm else None,
            context_window=int(cw) if cw is not None else None,
            default_max_tokens=int(dmt) if dmt is not None else None,
        )
    return out


def load_settings() -> Settings:
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)

    raw = _load_models_yaml()
    upstream: dict[str, str] = {k: str(v) for k, v in (raw.get("upstream") or {}).items()}
    models: dict[str, ModelSpec] = _parse_models(raw.get("models"))

    mem_raw = raw.get("memory") or {}
    emb = mem_raw.get("embedding") or {}
    memory = MemoryConfig(
        enabled=bool(mem_raw.get("enabled", True)),
        top_k=int(mem_raw.get("top_k", 6)),
        max_context_tokens=int(mem_raw.get("max_context_tokens", 8000)),
        summary_every_n_events=int(mem_raw.get("summary_every_n_events", 20)),
        summary_window=int(mem_raw.get("summary_window", 30)),
        summary_model=str(mem_raw.get("summary_model", "claude-sonnet")),
        embedding_primary_base_url=str(emb.get("primary_base_url", "http://127.0.0.1:8791")),
        embedding_primary_model=str(emb.get("primary_model", "text-embedding-3-small")),
        embedding_fallback=str(emb.get("fallback", "sentence-transformers")),
        embedding_fallback_model=str(emb.get("fallback_model", "all-MiniLM-L6-v2")),
        vector_backend=str(mem_raw.get("vector_backend", "faiss")).lower(),
        lance_table=str(mem_raw.get("lance_table", "memory")),
        lance_index_threshold=int(mem_raw.get("lance_index_threshold", 512)),
    )

    tr_raw = raw.get("tracing") or {}
    tracing = TraceConfig(
        enabled=bool(tr_raw.get("enabled", False)),
        dir=str(tr_raw.get("dir", "traces")),
        queue_max=int(tr_raw.get("queue_max", 1000)),
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ZAI_API_KEY is optional: only required when a 'zai' upstream is wired.
    zai_api_key = _optional_env("ZAI_API_KEY")
    if "zai" in upstream and not zai_api_key:
        raise SystemExit(
            "ERROR: a 'zai' upstream is configured in models.yaml but ZAI_API_KEY "
            "is missing or still a placeholder. Add it to .env (see .env.example)."
        )

    return Settings(
        bearer_token=_require_env("GATEWAY_BEARER_TOKEN"),
        anthropic_api_key=_require_env("ANTHROPIC_API_KEY"),
        minimax_api_key=_require_env("MINIMAX_API_KEY"),
        zai_api_key=zai_api_key,
        host=os.getenv("UMMG_HOST", "127.0.0.1"),
        port=int(os.getenv("UMMG_PORT", "8787")),
        upstream=upstream,
        models=models,
        memory=memory,
        tracing=tracing,
        data_dir=DATA_DIR,
    )
