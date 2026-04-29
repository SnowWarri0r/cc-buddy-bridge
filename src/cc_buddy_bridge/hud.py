"""One-line status renderer for Claude Code's ``statusLine`` setting.

Connects to the running daemon's IPC socket, asks for a snapshot, and
prints a compact line. Designed to complement claude-hud rather than
replace it: we focus on the stick-specific signals (BLE connection,
encryption, battery, pending button prompts) that Claude Code itself
doesn't know about.

On Unix: connects via Unix domain socket
On Windows: reads port from file and connects via TCP
"""

from __future__ import annotations

import json
import select
import socket
import sys
from pathlib import Path
from typing import Any, Optional

from .ipc import DEFAULT_SOCKET_PATH

# Bar rendering. Keep the width compact — claude-hud already fills most of
# the statusLine, and we need to fit next to it.
BAR_WIDTH = 8
BAR_FULL = "█"
BAR_EMPTY = "░"

# ANSI colour escapes. statusLine renders these; --ascii turns them off.
_ANSI_RESET = "\033[0m"
_ANSI_RED = "\033[31m"
_ANSI_YELLOW = "\033[33m"
_ANSI_GREEN = "\033[32m"


def _bar(pct: int, width: int = BAR_WIDTH) -> str:
    pct = max(0, min(100, int(pct)))
    filled = (pct * width + 50) // 100  # round to nearest
    return BAR_FULL * filled + BAR_EMPTY * (width - filled)


def _battery_color(pct: int) -> str:
    if pct <= 15:
        return _ANSI_RED
    if pct <= 40:
        return _ANSI_YELLOW
    return _ANSI_GREEN


def _battery_segment(pct: Optional[int], *, ascii_only: bool) -> Optional[str]:
    if not isinstance(pct, int):
        return None
    bar = _bar(pct)
    if ascii_only:
        # Fall back to plain ASCII; no colours, no low-batt icon.
        return f"[{bar.replace(BAR_FULL, '=').replace(BAR_EMPTY, '-')}] {pct}%"
    icon = "🪫" if pct <= 15 else "🔋"
    color = _battery_color(pct)
    return f"{icon} {color}{bar}{_ANSI_RESET} {pct}%"


def _query_state(socket_path: str, timeout: float = 0.5) -> Optional[dict[str, Any]]:
    """Best-effort: return the daemon's state dict or None if anything fails."""
    try:
        if sys.platform == "win32":
            # Windows: read port from file and connect via TCP
            port = int(Path(socket_path).read_text().strip())
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(("127.0.0.1", port))
        else:
            # Unix: connect via Unix domain socket
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(socket_path)

        s.sendall(b'{"evt":"get_state"}\n')
        buf = bytearray()
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
            if b"\n" in buf:
                break
        s.close()
    except (OSError, socket.timeout, ValueError):
        return None
    line = bytes(buf).split(b"\n", 1)[0]
    if not line:
        return None
    try:
        resp = json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not resp.get("ok"):
        return None
    return resp.get("state")


def format_line(state: Optional[dict[str, Any]], *, ascii_only: bool = False) -> str:
    """Render the state dict to a single line suitable for statusLine.

    When state is None (daemon off / unreachable) we say so quietly rather
    than printing nothing, so the user notices if the bridge died.
    """
    if state is None:
        return "buddy: off" if ascii_only else "🐾 off"

    if not state.get("ble_connected"):
        return "buddy: disc" if ascii_only else "🐾 ∅"

    # Pending permission takes over the line — visibility matters more than
    # battery when the user needs to press a button.
    pending = state.get("pending_tool")
    if pending:
        return (
            f"buddy: ASK {pending}"
            if ascii_only
            else f"🐾 ⚠ approve: {pending}"
        )

    parts: list[str] = []

    # Battery — rendered as a short coloured progress bar. Red ≤15, yellow ≤40.
    battery = _battery_segment(state.get("battery_pct"), ascii_only=ascii_only)
    if battery is not None:
        parts.append(battery)

    # Encryption
    sec = state.get("sec")
    if sec is True:
        parts.append("lock" if ascii_only else "🔒")
    elif sec is False:
        parts.append("UNSEC" if ascii_only else "⚠UNSEC")
    # sec=None (no status ack yet) → omit

    # Session activity (only if > 0)
    running = state.get("running") or 0
    if running:
        parts.append(f"{running}run")

    if ascii_only:
        return "buddy: " + " ".join(parts) if parts else "buddy: ok"
    return "🐾 " + " ".join(parts) if parts else "🐾 ok"


def run(ascii_only: bool = False, socket_path: Optional[str] = None) -> int:
    """Entry point called from cli.py. Consumes stdin if Claude Code sent any
    (statusLine passes session JSON via stdin) but doesn't block on it."""
    # Drain stdin non-blockingly. Claude Code's statusLine feeds JSON; we
    # don't use it today, but reading avoids leaving it unread in the pipe.
    try:
        if not sys.stdin.isatty():
            # Non-blocking drain — 100 ms timeout so we don't hang if no input.
            if select.select([sys.stdin], [], [], 0.1)[0]:
                sys.stdin.read()
    except (OSError, ValueError):
        pass

    path = socket_path or DEFAULT_SOCKET_PATH
    state = _query_state(path)
    sys.stdout.write(format_line(state, ascii_only=ascii_only) + "\n")
    return 0
