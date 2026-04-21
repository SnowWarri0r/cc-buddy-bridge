"""Tests for jsonl_tailer — focused on the parsing helpers rather than filesystem watching."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cc_buddy_bridge.jsonl_tailer import JSONLTailer, _record_is_today, _today_key


def test_record_is_today_matches_local_day():
    now = datetime.now(tz=timezone.utc)
    ts = now.isoformat().replace("+00:00", "Z")
    assert _record_is_today(ts, _today_key())


def test_record_is_today_rejects_yesterday():
    past = datetime.now(tz=timezone.utc) - timedelta(days=2)
    ts = past.isoformat().replace("+00:00", "Z")
    assert not _record_is_today(ts, _today_key())


def test_record_is_today_rejects_non_strings():
    assert not _record_is_today(None, _today_key())
    assert not _record_is_today(12345, _today_key())
    assert not _record_is_today("", _today_key())


def test_record_is_today_rejects_bad_iso():
    assert not _record_is_today("not-a-date", _today_key())
    assert not _record_is_today("2026/04/22", _today_key())


def test_record_is_today_handles_z_suffix():
    # Mid-day UTC → always the same day regardless of timezone (well, almost).
    # Use a timestamp fresh enough that it's definitely today in any tz.
    ts = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
    assert _record_is_today(ts, _today_key())


# ---- last_assistant_content ----

def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def _sync_sweep(root: Path) -> JSONLTailer:
    """Helper: spin up a tailer, run the initial sweep, return it. No file watching."""
    captured: list = []

    async def cb(c, t, e):
        captured.append((c, t, e))

    tailer = JSONLTailer(cb, root=root)
    asyncio.run(tailer._initial_sweep())
    return tailer


def test_last_assistant_content_captured(tmp_path: Path):
    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(jsonl, [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"output_tokens": 2},
        }},
        {"type": "user", "message": {"role": "user", "content": "bye"}},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "goodbye"}],
            "usage": {"output_tokens": 3},
        }},
    ])
    tailer = _sync_sweep(tmp_path)
    content = tailer.last_assistant_content(str(jsonl))
    assert content == [{"type": "text", "text": "goodbye"}]


def test_last_assistant_content_none_for_unknown_path(tmp_path: Path):
    tailer = _sync_sweep(tmp_path)
    assert tailer.last_assistant_content("/nowhere.jsonl") is None


def test_last_assistant_content_ignores_user_messages(tmp_path: Path):
    jsonl = tmp_path / "u.jsonl"
    _write_jsonl(jsonl, [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
    ])
    tailer = _sync_sweep(tmp_path)
    assert tailer.last_assistant_content(str(jsonl)) is None


def test_last_assistant_content_handles_missing_content(tmp_path: Path):
    """An assistant record without a content array shouldn't crash or overwrite prior content."""
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl, [
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "first"}],
            "usage": {"output_tokens": 1},
        }},
        {"type": "assistant", "message": {
            "role": "assistant",
            # No "content" field
            "usage": {"output_tokens": 1},
        }},
    ])
    tailer = _sync_sweep(tmp_path)
    content = tailer.last_assistant_content(str(jsonl))
    assert content == [{"type": "text", "text": "first"}]
