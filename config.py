"""Configuration loading. Reads .env (if present) and models.yaml.

Exposes a single `Settings` object built once at startup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
MODELS_PATH = ROOT / "models.yaml"
DATA_DIR = Path(os.getenv("UMMG_DATA_DIR", str(ROOT / "data")))


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


@dataclass(frozen=True)
class Settings:
    bearer_token: str
    anthropic_api_key: str
    minimax_api_key: str
    zai_api_key: str
    host: str
    port: int
    upstream: dict[str, str]
    models: dict[str, str]
    memory: MemoryConfig
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


def load_settings() -> Settings:
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)

    raw = _load_models_yaml()
    upstream: dict[str, str] = {k: str(v) for k, v in (raw.get("upstream") or {}).items()}
    models: dict[str, str] = {k: str(v["adapter"]) for k, v in (raw.get("models") or {}).items() if "adapter" in v}

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
        data_dir=DATA_DIR,
    )
