"""Tiny synchronous Unix-socket client used by hook scripts.

Hooks are short-lived subprocesses. We don't want to pay asyncio import cost
for every tool call — a stdlib-only sync client is faster and cleaner.

If the daemon is unreachable or slow, we return None so the caller can degrade
gracefully (i.e., don't block Claude Code's normal flow).
"""

from __future__ import annotations

import json
import os
import socket
import sys
from typing import Any, Optional

DEFAULT_SOCKET_PATH = os.environ.get(
    "CC_BUDDY_BRIDGE_SOCK",
    "/tmp/cc-buddy-bridge.sock",
)

# How long a hook is willing to wait for the daemon before giving up.
# PreToolUse overrides this to a much larger value for the BLE round-trip.
DEFAULT_TIMEOUT_SECS = 3.0


def read_hook_input() -> dict[str, Any]:
    """Read Claude Code's JSON hook payload from stdin."""
    data = sys.stdin.read()
    if not data:
        return {}
    try:
        return json.loads(data)
    except ValueError:
        return {}


def post(
    event: dict[str, Any],
    socket_path: str = DEFAULT_SOCKET_PATH,
    timeout: float = DEFAULT_TIMEOUT_SECS,
) -> Optional[dict[str, Any]]:
    """Send one JSON event, read one JSON response, close. Returns None on any error."""
    if not os.path.exists(socket_path):
        return None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(socket_path)
        s.sendall((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
        # Read until newline.
        buf = bytearray()
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
            if b"\n" in buf:
                break
        s.close()
    except (OSError, socket.timeout):
        return None
    line = bytes(buf).split(b"\n", 1)[0]
    if not line:
        return None
    try:
        return json.loads(line.decode("utf-8"))
    except ValueError:
        return None
