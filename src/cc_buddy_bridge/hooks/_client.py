"""Tiny synchronous transport client used by hook scripts.

Hooks are short-lived subprocesses, so the client side stays synchronous even
though the shared IPC transport can also start asyncio servers for the daemon.

Transport (Unix socket vs TCP loopback) is resolved by ``ipc.make_transport``;
the socket_path argument is forwarded as a *transport spec*. On POSIX this is
typically a path; on Windows it's ``host:port``.

If the daemon is unreachable or slow, we return ``None`` so the caller can
degrade gracefully (i.e., don't block Claude Code's normal flow).
"""

from __future__ import annotations

import json
import socket as _socket
import sys
from typing import Any, Optional

from ..ipc import default_spec, make_transport

# Back-compat for existing imports.
DEFAULT_SOCKET_PATH = default_spec()

# How long a hook is willing to wait for the daemon before giving up.
# PreToolUse overrides this to a much larger value for the BLE round-trip.
DEFAULT_TIMEOUT_SECS = 3.0


def _clean(obj: Any) -> Any:
    """Recursively replace lone surrogates in strings so downstream code stays clean."""
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def read_hook_input() -> dict[str, Any]:
    """Read Claude Code's JSON hook payload from stdin."""
    # Force UTF-8 regardless of the Windows console encoding (cp936/GBK).
    # Claude Code writes UTF-8 JSON; sys.stdin in text mode uses the locale
    # encoding on Windows, which silently produces Mojibake for CJK content.
    raw = sys.stdin.buffer.read()
    if not raw:
        return {}
    data = raw.decode("utf-8", errors="replace")
    try:
        return _clean(json.loads(data))
    except ValueError:
        return {}


def post(
    event: dict[str, Any],
    socket_path: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    connect_timeout: float = DEFAULT_TIMEOUT_SECS,
) -> Optional[dict[str, Any]]:
    """Send one JSON event, read one JSON response, close. Returns None on any error.

    ``connect_timeout`` caps the TCP handshake (default: same as ``timeout``).
    ``timeout`` caps waiting for the daemon's response once connected.
    Separating the two prevents port-exhaustion stalls from blocking for the
    full ``timeout`` value (e.g. 320 s for pretooluse) at the connect step.
    """
    s: Optional[_socket.socket] = None
    try:
        transport = make_transport(socket_path)
        s = transport.sync_connect(connect_timeout)
        # Switch to the (possibly longer) read timeout after connect succeeds.
        s.settimeout(timeout)
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
    except (OSError, _socket.timeout, ValueError):
        return None
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
    line = bytes(buf).split(b"\n", 1)[0]
    if not line:
        return None
    try:
        return json.loads(line.decode("utf-8"))
    except ValueError:
        return None
