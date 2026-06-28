"""Adapter package. Thin pass-through to upstream model servers.

Adapters DO NOT contain memory logic. They only forward OpenAI-shape requests.
"""
from __future__ import annotations

from .base import Adapter
from .anthropic import AnthropicAdapter
from .minimax import MinimaxAdapter
from .local import LocalAdapter
from .zai import ZaiAdapter


def build_adapter(name: str, base_url: str, api_key: str | None = None) -> Adapter:
    if name == "anthropic":
        return AnthropicAdapter(base_url=base_url, api_key=api_key)
    if name == "minimax":
        return MinimaxAdapter(base_url=base_url, api_key=api_key)
    if name == "local":
        return LocalAdapter(base_url=base_url)
    if name == "zai":
        return ZaiAdapter(base_url=base_url, api_key=api_key)
    raise ValueError(f"Unknown adapter '{name}'")


__all__ = ["Adapter", "AnthropicAdapter", "MinimaxAdapter", "LocalAdapter", "ZaiAdapter", "build_adapter"]