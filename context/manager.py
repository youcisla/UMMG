"""Context packet builder + token-aware truncation.

Builds a single system message containing:
  - the latest rolling summary (if any)
  - top-K retrieved memories (formatted compactly)
  - the active task label

Then prepends it to the request's messages and truncates the whole
payload to `max_context_tokens`. Token counting uses tiktoken's
cl100k_base by default; this is approximate for non-OpenAI models but
good enough to prevent overflow.
"""
from __future__ import annotations

import logging
from typing import Any

import tiktoken

log = logging.getLogger("ummg.context")

_ENCODER = None


def _encoder():
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def count_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        return len(_encoder().encode(text))
    except Exception:
        # Worst-case approximation
        return max(1, len(text) // 4)


def _format_memories(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return ""
    blocks: list[str] = []
    for m in memories:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        meta_bits: list[str] = []
        if m.get("role"):
            meta_bits.append(str(m["role"]))
        if m.get("model"):
            meta_bits.append(str(m["model"]))
        meta = "/".join(meta_bits) if meta_bits else "memory"
        score = m.get("_score")
        if isinstance(score, (int, float)):
            meta += f" (sim={score:.2f})"
        snippet = text[:400] + ("…" if len(text) > 400 else "")
        blocks.append(f"- [{meta}] {snippet}")
    return "\n".join(blocks)


class ContextManager:
    def __init__(self, max_context_tokens: int = 8000) -> None:
        self.max_context_tokens = max_context_tokens

    def build_packet(
        self,
        memories: list[dict[str, Any]],
        latest_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Returns the system message to prepend."""
        sections: list[str] = [
            "You are part of UMMG (Unified Model Memory Gateway). "
            "You have NO persistent memory of your own; the gateway injects "
            "the following context into every request. Treat it as authoritative.",
        ]
        if latest_summary and latest_summary.get("text"):
            sections.append("## ROLLING SUMMARY\n" + latest_summary["text"].strip())
        mem_block = _format_memories(memories)
        if mem_block:
            sections.append("## RELEVANT PAST MEMORIES\n" + mem_block)
        return {
            "role": "system",
            "content": "\n\n".join(sections),
        }

    def truncate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Drop oldest messages until total tokens <= max_context_tokens.

        Keeps: the most recent system packet (index 0), the most recent
        user message, and as many recent turns as fit.
        """
        messages = list(payload.get("messages") or [])
        if not messages:
            return payload

        budget = self.max_context_tokens

        # Always keep the first message (our system packet) and the last user.
        # Drop oldest non-system messages first.
        # 1) compute total
        def total() -> int:
            return sum(count_tokens((m.get("content") or "")) for m in messages)

        # Reserve room for the model's reply by trimming aggressively.
        # Allocate: max_context_tokens - reply_budget (1500 default)
        reply_budget = 1500
        soft_limit = max(512, budget - reply_budget)

        # Always keep at least the system packet (index 0) and the most recent user message.
        if len(messages) >= 2 and messages[0].get("role") == "system":
            protected_idx = {0, len(messages) - 1}
        else:
            protected_idx = {len(messages) - 1}

        # Drop from the middle, oldest-first, until under budget.
        changed = True
        while total() > soft_limit and changed:
            changed = False
            for i in range(1, len(messages) - 1):
                if i in protected_idx:
                    continue
                # Find first non-protected index from the start of the inner range
                if i not in protected_idx:
                    # Drop a chunk of older middle messages
                    del messages[i]
                    protected_idx = {0, len(messages) - 1} if messages and messages[0].get("role") == "system" else {len(messages) - 1}
                    changed = True
                    break

        payload = {**payload, "messages": messages}
        return payload