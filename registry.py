"""Model registry. Maps friendly model name -> adapter (+ optional metadata).

Loads from models.yaml at startup (config.Settings.models). Values are
ModelSpec objects, but plain ``{name: adapter_str}`` mappings are also
accepted for backward compatibility and ease of testing.
"""
from __future__ import annotations

from typing import Any


class ModelRegistry:
    def __init__(self, models: dict[str, Any], upstream: dict[str, str]) -> None:
        self._upstream = dict(upstream)
        self._specs: dict[str, Any] = {}
        self._m2a: dict[str, str] = {}
        for name, spec in models.items():
            if isinstance(spec, str):
                # Legacy / test form: {name: adapter}
                self._m2a[name] = spec
            else:
                # ModelSpec-like: anything exposing `.adapter`
                self._specs[name] = spec
                self._m2a[name] = spec.adapter
        self._adapter_models: dict[str, list[str]] = {}
        for model, adapter in self._m2a.items():
            self._adapter_models.setdefault(adapter, []).append(model)

    def resolve_adapter(self, model: str) -> str:
        adapter = self._m2a.get(model)
        if not adapter:
            # Prefix fallback for ids not explicitly registered.
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

    def spec_for(self, model: str) -> Any | None:
        """Return the ModelSpec for an explicitly-registered model, else None.

        Prefix-matched (unregistered) models have no spec; callers should
        treat None as "no per-model overrides — forward as-is".
        """
        return self._specs.get(model)

    def native_model_for(self, model: str) -> str | None:
        spec = self._specs.get(model)
        return getattr(spec, "native_model", None) if spec else None

    def context_window_for(self, model: str) -> int | None:
        spec = self._specs.get(model)
        return getattr(spec, "context_window", None) if spec else None

    def default_max_tokens_for(self, model: str) -> int | None:
        spec = self._specs.get(model)
        return getattr(spec, "default_max_tokens", None) if spec else None

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
