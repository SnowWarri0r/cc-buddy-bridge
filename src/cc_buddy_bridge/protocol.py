"""Serialization for the Hardware Buddy BLE wire protocol.

Matches the JSON schemas in the claude-desktop-buddy REFERENCE.md.
Everything is newline-terminated UTF-8 JSON.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Optional

from .state import State

# Nordic UART Service UUIDs (standard)
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # central → peripheral (we write)
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # peripheral → central (we notify)

# How often we send a keepalive heartbeat if nothing else changed (seconds).
HEARTBEAT_KEEPALIVE = 10.0

# Size cap for turn events per REFERENCE.md (4KB after UTF-8 encoding).
TURN_EVENT_MAX_BYTES = 4096

# Max chars per entry — keep the stick's line buffer happy.
ENTRY_TEXT_MAX = 80


def build_heartbeat(state: State, msg: Optional[str] = None) -> dict[str, Any]:
    """Build a heartbeat snapshot dict ready for json.dumps + b'\\n'."""
    pending = state.first_pending()
    snapshot: dict[str, Any] = {
        "total": state.total,
        "running": state.running_count,
        "waiting": state.waiting_count,
        "msg": msg if msg is not None else _default_msg(state, pending),
        "entries": [_format_entry(e.at, e.text) for e in state.entries],
        "tokens": state.tokens_cumulative,
        "tokens_today": state.tokens_today,
    }
    if pending is not None:
        snapshot["prompt"] = {
            "id": pending.tool_use_id,
            "tool": pending.tool_name,
            "hint": pending.hint[:120],
        }
    return snapshot


def build_turn_event(role: str, content: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Build a one-shot turn event. Returns None if it would exceed TURN_EVENT_MAX_BYTES."""
    evt = {"evt": "turn", "role": role, "content": content}
    encoded = json.dumps(evt, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > TURN_EVENT_MAX_BYTES:
        return None
    return evt


def build_time_sync() -> dict[str, Any]:
    """Desktop sends on (re)connect: epoch seconds + timezone offset seconds."""
    now = int(time.time())
    offset = int(datetime.now().astimezone().utcoffset().total_seconds())  # type: ignore[union-attr]
    return {"time": [now, offset]}


def build_owner(name: str) -> dict[str, Any]:
    return {"cmd": "owner", "name": name}


def build_name(device_name: str) -> dict[str, Any]:
    return {"cmd": "name", "name": device_name}


def encode(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


# ---- line reassembly for stick → daemon stream ----

class LineAssembler:
    """BLE notifications fragment at the MTU boundary. Collect until newline."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        self._buf.extend(chunk)
        out: list[dict[str, Any]] = []
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line.decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                # Drop malformed line rather than poison the stream.
                continue
        return out


# ---- internals ----

def _format_entry(at: float, text: str) -> str:
    # Format: "HH:MM text" — REFERENCE.md shows "10:42 git push".
    hhmm = datetime.fromtimestamp(at).strftime("%H:%M")
    text = text.replace("\n", " ").strip()
    if len(text) > ENTRY_TEXT_MAX:
        text = text[: ENTRY_TEXT_MAX - 1] + "…"
    return f"{hhmm} {text}"


def _default_msg(state: State, pending) -> str:
    if pending is not None:
        return f"approve: {pending.tool_name}"
    if state.running_count > 0:
        return f"{state.running_count} running"
    if state.total > 0:
        return f"{state.total} idle"
    return "no sessions"
