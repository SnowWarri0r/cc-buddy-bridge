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


SOUND_FILE = "/System/Library/Sounds/Glass.aiff"


def notify_turn_complete(*, subtitle: str = "", session_id: str = "") -> None:
    """Pop a 'Claude finished' banner + play a sound.

    Sound is played via ``afplay`` in a separate Popen instead of the
    AppleScript ``sound name "Glass"`` parameter — that latter route
    depends on Script Editor having Notification Center sound permission,
    which is off by default on recent macOS versions and silently swallows
    the audio. ``afplay`` doesn't go through Notification Center at all.
    """
    if platform.system() != "Darwin":
        return
    title = "cc-buddy-bridge"
    body = "Claude finished — tap to refocus"
    parts = [
        f'display notification {_q(body)}',
        f'with title {_q(title)}',
    ]
    if subtitle:
        parts.append(f'subtitle {_q(subtitle)}')
    script = " ".join(parts)
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError) as e:
        log.debug("notify banner failed: %s", e)
    try:
        subprocess.Popen(
            ["afplay", SOUND_FILE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError) as e:
        log.debug("notify sound failed: %s", e)
    log.debug("notify_turn_complete fired (session=%s)", session_id)


def _q(text: str) -> str:
    """AppleScript single-line string literal — escape backslashes + quotes."""
    safe = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{safe}"'
