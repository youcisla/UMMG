"""Directive 4 verification — trace recorder masking + fail-open + teich guard."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from trace import TraceRecorder, export_dataset, teich_available
from trace.recorder import _mask


def test_mask_redacts_keys_and_values() -> None:
    payload = {
        "Authorization": "Bearer sk-ant-supersecretvalue123",
        "nested": {"api_key": "abcd", "ok": "keepme"},
        "messages": [{"role": "user", "content": "my key is sk-1234567890abcdef"}],
        "x-api-key": "zk-live-zzz",
    }
    masked = _mask(payload)
    assert masked["Authorization"] == "***REDACTED***"
    assert masked["nested"]["api_key"] == "***REDACTED***"
    assert masked["nested"]["ok"] == "keepme"
    assert masked["x-api-key"] == "***REDACTED***"
    # secret-looking value under a benign key is scrubbed too
    assert "sk-1234567890abcdef" not in masked["messages"][0]["content"]


def test_recorder_writes_masked_jsonl() -> None:
    async def run() -> dict:
        with tempfile.TemporaryDirectory() as td:
            rec = TraceRecorder(Path(td), enabled=True)
            await rec.start()
            rec.record({
                "model": "glm-5.2",
                "request": {"messages": [{"role": "user", "content": "hi"}],
                            "Authorization": "Bearer sk-ant-leak"},
                "response": {"content": "pong"},
            })
            await rec.stop()
            files = list(Path(td).glob("traces-*.jsonl"))
            assert len(files) == 1
            line = files[0].read_text(encoding="utf-8").strip()
            return json.loads(line)

    rec = asyncio.run(run())
    assert rec["model"] == "glm-5.2"
    assert rec["request"]["Authorization"] == "***REDACTED***"
    assert rec["response"]["content"] == "pong"
    assert "ts" in rec


def test_recorder_disabled_is_noop() -> None:
    async def run() -> int:
        with tempfile.TemporaryDirectory() as td:
            rec = TraceRecorder(Path(td), enabled=False)
            await rec.start()
            rec.record({"model": "x"})  # must not raise, must not write
            await rec.stop()
            return len(list(Path(td).glob("*.jsonl")))

    assert asyncio.run(run()) == 0


def test_teich_export_degrades_gracefully() -> None:
    # teich is not installed in CI; export must return False, never raise.
    with tempfile.TemporaryDirectory() as td:
        ok = export_dataset(Path(td), Path(td) / "out.jsonl")
        if not teich_available():
            assert ok is False
        else:
            assert isinstance(ok, bool)
