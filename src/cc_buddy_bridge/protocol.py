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

# Maps CJK target → Python codec name. Used by `sanitize_for_stick` and
# `encode` to switch the wire format when the user has flashed a fork-only
# firmware build that ships the matching font (see claude-desktop-buddy
# fork: `m5stickc-plus-cjk-zh-cn` / `-zh-tw` / `-ja`).
CJK_CODECS: dict[str, str] = {
    "zh-CN": "gbk",
    "zh-TW": "big5",
    "ja":    "shift_jis",
}


def build_heartbeat(state: State, msg: Optional[str] = None, codec: Optional[str] = None) -> dict[str, Any]:
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
        "msg": sanitize_for_stick(msg if msg is not None else _default_msg(state, pending), codec),
        "entries": [sanitize_for_stick(_format_entry(e.at, e.text, codec), codec) for e in reversed(state.entries)],
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
            "tool": sanitize_for_stick(pending.tool_name, codec),
            "hint": sanitize_for_stick(truncate_utf8_bytes(pending.hint, 60), codec),
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


def encode(obj: dict[str, Any], codec: Optional[str] = None) -> bytes:
    """Serialize obj as one-line JSON, newline-terminated.

    With ``codec`` unset, falls back to standard UTF-8 JSON (json.dumps).
    With ``codec`` set to a non-UTF-8 name (``gbk`` / ``big5`` / ``shift_jis``),
    string *values* are encoded in that codec instead — the bytes go straight
    into the JSON string between quotes, which ArduinoJson 7 stores verbatim.
    Keys, numeric literals, structural punctuation stay ASCII. Used by the
    CJK firmware build, where the on-device font tables look up bytes in
    these legacy double-byte encodings.
    """
    if codec is None or codec == "utf-8":
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    parts: list[bytes] = []
    _encode_value(obj, codec, parts)
    parts.append(b"\n")
    return b"".join(parts)


def _encode_value(v: Any, codec: str, out: list[bytes]) -> None:
    if isinstance(v, dict):
        out.append(b"{")
        first = True
        for k, vv in v.items():
            if not first:
                out.append(b",")
            first = False
            _encode_json_string(str(k), "ascii", out)
            out.append(b":")
            _encode_value(vv, codec, out)
        out.append(b"}")
    elif isinstance(v, list):
        out.append(b"[")
        first = True
        for item in v:
            if not first:
                out.append(b",")
            first = False
            _encode_value(item, codec, out)
        out.append(b"]")
    elif isinstance(v, str):
        _encode_json_string(v, codec, out)
    elif isinstance(v, bool):
        out.append(b"true" if v else b"false")
    elif isinstance(v, (int, float)):
        out.append(str(v).encode("ascii"))
    elif v is None:
        out.append(b"null")
    else:
        _encode_json_string(str(v), codec, out)


def _encode_json_string(s: str, codec: str, out: list[bytes]) -> None:
    """Write a JSON-escaped string with bytes encoded in ``codec``.

    Chars unrepresentable in the codec become '?' (codec error='replace').
    Inside the quoted body we only escape JSON's metabytes: backslash,
    double-quote, and C0 controls. Codec high-bit bytes go through raw.
    """
    out.append(b'"')
    raw = s.encode(codec, errors="replace")
    for byte in raw:
        if byte == 0x22:        # "
            out.append(b'\\"')
        elif byte == 0x5C:      # \
            out.append(b"\\\\")
        elif byte == 0x08:
            out.append(b"\\b")
        elif byte == 0x09:
            out.append(b"\\t")
        elif byte == 0x0A:
            out.append(b"\\n")
        elif byte == 0x0D:
            out.append(b"\\r")
        elif byte < 0x20:
            out.append(f"\\u{byte:04x}".encode("ascii"))
        else:
            out.append(bytes([byte]))
    out.append(b'"')


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

def sanitize_for_stick(text: str, codec: Optional[str] = None) -> str:
    """Strip characters the stick's font can't safely render.

    Two regimes, controlled by ``codec``:

    - ``codec is None`` (stock firmware): strip everything outside printable
      ASCII + tab. CJK becomes ``?`` because the default 5×7 font has no
      glyphs for it and history shows the firmware crashes (LoadProhibited
      from out-of-bounds font-table reads) when high-bit codepoints reach
      the GLCD path. Two earlier sanitizer revisions both bit us — see git
      log on this file and the README quirk #1 narrative.

    - ``codec in CJK_CODECS.values()`` (CJK firmware variant flashed):
      pass through every character representable in the target codec
      (``gbk`` covers GB2312, ``big5`` covers BIG5, ``shift_jis`` covers
      JIS X 0208). Characters not in the codec — most emoji, mismatched
      scripts — become ``?`` via Python's ``errors='replace'`` codec
      mechanism. The downstream encode() call serializes string values as
      target-codec bytes, which the firmware's font tables index directly.
    """
    if not text:
        return text
    if codec is None or codec == "utf-8":
        out: list[str] = []
        for ch in text:
            cp = ord(ch)
            if 0x20 <= cp <= 0x7E:
                out.append(ch)
            elif ch == "\t":
                out.append(ch)
            else:
                out.append(UNRENDERABLE_REPLACEMENT)
        return "".join(out)
    # Codec-aware regime: round-trip through the target encoding so any
    # character not representable in it becomes the codec's replacement byte
    # (Python writes a literal '?' on encode-error='replace'), and the result
    # decodes back to a string of representable characters.
    try:
        return text.encode(codec, errors="replace").decode(codec, errors="replace")
    except LookupError:
        # Unknown codec — fall back to the conservative ASCII policy.
        return sanitize_for_stick(text, None)


# ---- internals ----

def _format_entry(at: float, text: str, codec: Optional[str] = None) -> str:
    """Format an entry for the wire: ``HH:MM text``. REFERENCE.md shows
    ``10:42 git push``. The codec argument is reserved for callers that
    want a codec-aware variant later; it's accepted-but-unused right now
    because the firmware does its own pixel-aware wrap on incoming lines.
    """
    del codec  # unused; the wrap renderer handles long lines downstream.
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
