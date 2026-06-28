"""Model registry. Maps friendly model name -> adapter name.

Loads from models.yaml at startup (config.Settings.models).
"""
from __future__ import annotations

from typing import Optional


class ModelRegistry:
    def __init__(self, model_to_adapter: dict[str, str], upstream: dict[str, str]) -> None:
        self._m2a = dict(model_to_adapter)
        self._upstream = dict(upstream)
        self._adapter_models: dict[str, list[str]] = {}
        for model, adapter in self._m2a.items():
            self._adapter_models.setdefault(adapter, []).append(model)

    def resolve_adapter(self, model: str) -> str:
        adapter = self._m2a.get(model)
        if not adapter:
            # Allow prefix matching: "claude-*" -> anthropic, "minimax-*" -> minimax, "local-*" -> local
            if model.startswith("claude-"):
                return "anthropic"
            if model.startswith("glm-"):
                return "zai"
            if model.startswith("minimax-"):
                return "minimax"
            if model.startswith("local-"):
                return "local"
            raise UnknownModelError(model, list(self._m2a.keys()))
        return adapter

    def upstream_for(self, adapter: str) -> str:
        url = self._upstream.get(adapter)
        if not url:
            raise KeyError(f"No upstream configured for adapter '{adapter}'")
        return url

    def list_models(self) -> list[str]:
        return sorted(self._m2a.keys())

    def list_by_adapter(self, adapter: str) -> list[str]:
        return list(self._adapter_models.get(adapter, []))


class UnknownModelError(KeyError):
    def __init__(self, model: str, known: list[str]) -> None:
        super().__init__(model)
        self.model = model
        self.known = known

    def __str__(self) -> str:
        return (
            f"Unknown model '{self.model}'. "
            f"Known models: {', '.join(self.known)}"
        )