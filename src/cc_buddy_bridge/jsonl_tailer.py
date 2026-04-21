"""Watch Claude Code transcript files under ~/.claude/projects/ for token + entry updates.

Hooks don't expose cumulative token counts, so we parse them out of the session
JSONL files. Each assistant message has a `usage.output_tokens` field that we
sum per-file, then aggregate across all files for the grand total.

We also pull short snippets (user prompts + tool-call summaries) to populate
the stick's `entries` list.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from watchfiles import Change, awatch

log = logging.getLogger(__name__)

TRANSCRIPT_ROOT = Path.home() / ".claude" / "projects"

# Callback: async (tokens_cumulative, tokens_today, new_entries: list[tuple[float,str]]) -> None
TokensCallback = Callable[[int, int, list[tuple[float, str]]], Awaitable[None]]


class JSONLTailer:
    """Incrementally reads every transcript JSONL, tracks file offsets so we only
    process new bytes, and recomputes aggregates on change."""

    def __init__(self, on_update: TokensCallback, root: Path = TRANSCRIPT_ROOT) -> None:
        self.on_update = on_update
        self.root = root
        # file path → (offset, session_tokens_output, per_day_tokens_output)
        self._offsets: dict[str, int] = {}
        self._tokens_per_file: dict[str, int] = {}
        # day_key → sum of tokens_today across files (day_key is YYYY-MM-DD local)
        self._day_key = _today_key()
        self._today_tokens_per_file: dict[str, int] = {}
        # file path → last parsed assistant content array. Used by the daemon to
        # emit a `turn` event over BLE when the Stop hook fires.
        self._last_assistant_content: dict[str, list] = {}

    async def run(self) -> None:
        if not self.root.exists():
            log.warning("transcript root %s does not exist; creating", self.root)
            self.root.mkdir(parents=True, exist_ok=True)

        # Initial sweep so aggregates are hot before any file event fires.
        await self._initial_sweep()
        await self._emit()

        # Watch for changes. watchfiles yields sets of (Change, path).
        try:
            async for changes in awatch(str(self.root), recursive=True, stop_event=None):
                await self._handle_changes(changes)
                await self._emit()
        except Exception:  # noqa: BLE001
            log.exception("jsonl tailer crashed")

    async def _initial_sweep(self) -> None:
        for p in self.root.rglob("*.jsonl"):
            try:
                self._process_file(str(p))
            except Exception as e:  # noqa: BLE001
                log.debug("initial sweep of %s failed: %s", p, e)

    async def _handle_changes(self, changes: set[tuple[Change, str]]) -> None:
        for change, path_str in changes:
            if not path_str.endswith(".jsonl"):
                continue
            if change == Change.deleted:
                self._offsets.pop(path_str, None)
                self._tokens_per_file.pop(path_str, None)
                self._today_tokens_per_file.pop(path_str, None)
                continue
            try:
                self._process_file(path_str)
            except Exception as e:  # noqa: BLE001
                log.debug("process %s failed: %s", path_str, e)

    def _process_file(self, path: str) -> None:
        """Read new bytes since last offset. Parse each line, accumulate tokens + entries."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        start = self._offsets.get(path, 0)
        if start > size:
            # File was truncated/rotated. Reset.
            start = 0
            self._tokens_per_file.pop(path, None)
            self._today_tokens_per_file.pop(path, None)
        if start == size:
            return
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(size - start)
        # Parse complete lines only — if the last line is partial, leave it.
        last_nl = data.rfind(b"\n")
        if last_nl < 0:
            return
        consumed = data[: last_nl + 1]
        self._offsets[path] = start + len(consumed)

        current_day = _today_key()
        if current_day != self._day_key:
            # Day rolled over — reset today-only counters.
            self._day_key = current_day
            self._today_tokens_per_file.clear()

        for raw in consumed.splitlines():
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            self._consume_obj(path, obj, current_day)

    def _consume_obj(self, path: str, obj: dict[str, Any], current_day: str) -> None:
        # Claude Code transcript entries can nest differently across versions.
        # Check a few common shapes.
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        if not isinstance(msg, dict):
            return

        # Track the latest assistant content for turn-event emission.
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, list):
                self._last_assistant_content[path] = content

        usage = msg.get("usage")
        if not isinstance(usage, dict):
            return
        out = int(usage.get("output_tokens") or 0)
        if not out:
            return
        self._tokens_per_file[path] = self._tokens_per_file.get(path, 0) + out
        # Only attribute to today's counter if the record's own timestamp falls
        # within the current local day. Records without a timestamp contribute
        # to cumulative only.
        if _record_is_today(obj.get("timestamp"), current_day):
            self._today_tokens_per_file[path] = self._today_tokens_per_file.get(path, 0) + out

    async def _emit(self) -> None:
        cumulative = sum(self._tokens_per_file.values())
        today = sum(self._today_tokens_per_file.values())
        # Entries aren't implemented via tailer yet — hook events feed them directly.
        # Keeping the signature for future expansion.
        await self.on_update(cumulative, today, [])

    def last_assistant_content(self, transcript_path: str) -> list | None:
        """Return the most recently parsed assistant content array for a transcript,
        or None if we haven't seen an assistant message in it yet."""
        return self._last_assistant_content.get(transcript_path)


def _today_key() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _record_is_today(ts: Any, current_day: str) -> bool:
    """Parse an ISO 8601 timestamp (optionally Z-suffixed) and return True if
    it falls on the current local day. Claude Code writes timestamps in UTC
    with a 'Z' suffix; we convert to local time before comparing."""
    if not isinstance(ts, str) or not ts:
        return False
    try:
        # fromisoformat doesn't accept a trailing 'Z' until 3.11; normalize.
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.astimezone().strftime("%Y-%m-%d") == current_day
