"""Live Z.ai connectivity probe (run manually, requires a real key).

This is intentionally NOT part of the offline test suite — it makes a real
network call to Z.ai. Run it on the Windows host once ZAI_API_KEY is set:

    python scripts/zai_smoke.py
    python scripts/zai_smoke.py --model glm-4.7 --base https://api.z.ai/api/coding/paas/v4

Exits 0 on a well-formed completion, non-zero otherwise. No secrets are printed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Allow running from repo root: make the package importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.zai import ZaiAdapter  # noqa: E402


async def _main() -> int:
    ap = argparse.ArgumentParser(description="Z.ai live smoke test")
    ap.add_argument("--model", default="glm-5.2")
    ap.add_argument("--base", default="https://api.z.ai/api/coding/paas/v4")
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    key = os.getenv("ZAI_API_KEY", "").strip()
    if not key:
        print("FAIL: ZAI_API_KEY not set in environment", file=sys.stderr)
        return 2

    adapter = ZaiAdapter(args.base, api_key=key)
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
        "max_tokens": 256,  # reasoning models spend budget on hidden reasoning_tokens
        "temperature": 0,
    }
    try:
        resp = await adapter.chat(payload, timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001 - probe reports any failure cleanly
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        text = resp["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        print(f"FAIL: unexpected response shape: {list(resp)[:8]}", file=sys.stderr)
        return 1

    print(f"OK  model={args.model}  reply={text!r}")
    usage = resp.get("usage") or {}
    if usage:
        print(f"    usage: {usage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
