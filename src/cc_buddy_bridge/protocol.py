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

# Max UTF-8 bytes for the text portion of each entry (before the "HH:MM " prefix).
# CJK characters are 3 bytes each, so 60 bytes ≈ 20 CJK chars or 60 ASCII chars.
# Enforced in bytes (not chars) so the firmware's line buffer never overflows.
ENTRY_MAX_BYTES = 60

# Replacement character used when we strip a codepoint the stick can't render.
# Keep it to 1 ASCII char so it doesn't blow up byte budgets or fall into the
# same trap the original codepoint would have (multi-byte UTF-8 sequences that
# bitmap fonts can't map).
UNRENDERABLE_REPLACEMENT = "?"


def build_heartbeat(state: State, msg: Optional[str] = None) -> dict[str, Any]:
    """Build a heartbeat snapshot dict ready for json.dumps + b'\\n'.

    Entry order on the wire is **oldest-first**. The reference firmware's
    drawHUD treats ``lines[n-1]`` as the newest (highlighted, shown at the
    bottom of the 3-row HUD window); it'd otherwise hide our newest entry at
    the top of its wrapped buffer. We keep ``state.entries`` newest-first
    internally because that's cheaper to prepend to — reverse on serialize.
    """
    pending = state.first_pending()
    snapshot: dict[str, Any] = {
        "total": state.total,
        "running": state.running_count,
        "waiting": state.waiting_count,
        "msg": sanitize_for_stick(msg if msg is not None else _default_msg(state, pending)),
        "entries": [sanitize_for_stick(_format_entry(e.at, e.text)) for e in reversed(state.entries)],
        "tokens": state.tokens_cumulative,
        "tokens_today": state.tokens_today,
    }
    # Pulse the firmware's celebrate animation (confetti + bouncing) for the
    # few seconds after a turn ends. Honoured by data.h:_applyJson which maps
    # this field onto recentlyCompleted, picked up by main.cpp:derive.
    if state.is_celebrating:
        snapshot["completed"] = True
    if pending is not None:
        snapshot["prompt"] = {
            "id": pending.tool_use_id,  # tool_use_id is ASCII by construction
            "tool": sanitize_for_stick(pending.tool_name),
            "hint": sanitize_for_stick(truncate_utf8_bytes(pending.hint, 60)),
        }
    return snapshot


def build_turn_event(role: str, content: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Build a one-shot turn event. Returns None if it would exceed TURN_EVENT_MAX_BYTES.

    Recursively sanitizes string values inside the content array so the stick
    doesn't receive glyphs its bitmap font can't render (which, empirically,
    crashes the firmware)."""
    evt = {"evt": "turn", "role": role, "content": _sanitize_content(content)}
    encoded = json.dumps(evt, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > TURN_EVENT_MAX_BYTES:
        return None
    return evt


def _sanitize_content(obj: Any) -> Any:
    """Deep-copy helper that sanitizes every string leaf."""
    if isinstance(obj, str):
        return sanitize_for_stick(obj)
    if isinstance(obj, list):
        return [_sanitize_content(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_content(v) for k, v in obj.items()}
    return obj


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


# ---- sanitization ----

def sanitize_for_stick(text: str) -> str:
    """Strip characters the stick's font can't safely render.

    Firmware now ships a CJK-capable font, so BMP characters (U+0000–U+FFFF)
    including CJK unified ideographs, fullwidth punctuation, and kana are all
    renderable. We still strip:
      - C0/C1 control characters (except tab) — no glyph, undefined behaviour
      - Supplementary-plane codepoints (U+10000+) such as emoji — font table
        only covers BMP; these would still cause an out-of-range index fault
      - Lone surrogates (U+D800–U+DFFF) — invalid as standalone codepoints
    """
    if not text:
        return text
    out = []
    for ch in text:
        cp = ord(ch)
        if cp > 0xFFFF:
            out.append(UNRENDERABLE_REPLACEMENT)
        elif 0xD800 <= cp <= 0xDFFF:
            out.append(UNRENDERABLE_REPLACEMENT)
        elif cp < 0x20 and ch != "\t":
            out.append(UNRENDERABLE_REPLACEMENT)
        else:
            out.append(ch)
    return "".join(out)


# ---- internals ----

def _format_entry(at: float, text: str) -> str:
    # Format: "HH:MM text" — REFERENCE.md shows "10:42 git push".
    hhmm = datetime.fromtimestamp(at).strftime("%H:%M")
    text = text.replace("\n", " ").strip()
    return f"{hhmm} {truncate_utf8_bytes(text, ENTRY_MAX_BYTES)}"


def truncate_utf8_bytes(text: str, max_bytes: int) -> str:
    """Truncate text so its UTF-8 encoding fits within max_bytes, appending '…' if cut.

    Truncates at a codepoint boundary so no Chinese character is split.
    '…' is 3 bytes; budget is reduced accordingly before slicing.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    budget = max_bytes - 3  # reserve space for '…' (3 UTF-8 bytes)
    if budget <= 0:
        return "…"
    end = budget
    # If the byte right after the cut is a continuation byte (10xxxxxx), the
    # boundary falls mid-codepoint. Walk back to the lead byte of that
    # incomplete sequence and exclude the whole codepoint.
    while end > 0 and (encoded[end] & 0b1100_0000) == 0b1000_0000:
        end -= 1
    return encoded[:end].decode("utf-8") + "…"


def _default_msg(state: State, pending) -> str:
    if pending is not None:
        return f"approve: {pending.tool_name}"
    if state.running_count > 0:
        return f"{state.running_count} running"
    if state.total > 0:
        return f"{state.total} idle"
    return "no sessions"
