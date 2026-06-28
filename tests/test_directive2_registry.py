"""Directive 2 offline verification — schema rewrite + registry threading."""
from __future__ import annotations

import config
from config import ModelSpec
from registry import ModelRegistry


def test_models_yaml_parses_all_required_models() -> None:
    raw = config._load_models_yaml()
    specs = config._parse_models(raw.get("models"))
    required = {
        "claude-fable-5", "glm-5.2", "glm-4.7",
        "minimax-m3", "qwythos-9b", "gemma4-12b-agentic",
    }
    assert required.issubset(specs.keys())
    # GLM reasoning models must carry a real output budget.
    assert specs["glm-5.2"].default_max_tokens and specs["glm-5.2"].default_max_tokens >= 4096
    assert specs["glm-5.2"].adapter == "zai"


def test_registry_specs_and_accessors() -> None:
    specs = {
        "glm-5.2": ModelSpec(adapter="zai", native_model="glm-5.2",
                             context_window=200000, default_max_tokens=8192),
        "claude-opus": ModelSpec(adapter="anthropic", context_window=200000,
                                 default_max_tokens=8192),
    }
    r = ModelRegistry(specs, {"zai": "https://api.z.ai/api/coding/paas/v4"})
    assert r.resolve_adapter("glm-5.2") == "zai"
    assert r.native_model_for("glm-5.2") == "glm-5.2"
    assert r.context_window_for("glm-5.2") == 200000
    assert r.default_max_tokens_for("glm-5.2") == 8192
    # claude-opus has no native_model -> None (router won't substitute)
    assert r.native_model_for("claude-opus") is None
    # prefix-matched, unregistered model -> no spec
    assert r.spec_for("glm-9.9") is None
    assert r.resolve_adapter("glm-9.9") == "zai"


def test_registry_accepts_legacy_str_mapping() -> None:
    # Directive 1 tests construct the registry with plain strings; keep that working.
    r = ModelRegistry({"claude-opus": "anthropic"}, {})
    assert r.resolve_adapter("claude-opus") == "anthropic"
    assert r.spec_for("claude-opus") is None
