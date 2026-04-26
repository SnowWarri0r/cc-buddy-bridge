"""Best-effort macOS-native notifications for assistant turn completion.

Uses ``osascript`` so we get the same system notification banner Claude
Code's other macOS-aware tools produce, plus the user's configured Notification
Center sound. Fired fire-and-forget — never blocks the IPC handler.

Silently no-ops on non-macOS so tests / Linux contributors don't pay for
this path.
"""

from __future__ import annotations

import logging
import platform
import shlex
import subprocess

log = logging.getLogger(__name__)


def notify_turn_complete(*, subtitle: str = "", session_id: str = "") -> None:
    """Pop a 'Claude finished' banner. Optional subtitle (e.g. last entry)."""
    if platform.system() != "Darwin":
        return
    title = "cc-buddy-bridge"
    body = "Claude finished — tap to refocus"
    sound = "Glass"
    parts = [
        f'display notification {_q(body)}',
        f'with title {_q(title)}',
    ]
    if subtitle:
        parts.append(f'subtitle {_q(subtitle)}')
    parts.append(f'sound name {_q(sound)}')
    script = " ".join(parts)
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.debug("notify_turn_complete fired (session=%s)", session_id)
    except (FileNotFoundError, OSError) as e:
        log.debug("notify_turn_complete failed: %s", e)


def _q(text: str) -> str:
    """AppleScript single-line string literal — escape backslashes + quotes."""
    safe = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{safe}"'
