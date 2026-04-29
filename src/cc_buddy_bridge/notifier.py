"""Best-effort native notifications for assistant turn completion.

macOS: Uses osascript for notification banner + afplay for sound
Windows: Uses PowerShell for toast notification + winsound for beep
Linux: Silently no-ops

Fired fire-and-forget — never blocks the IPC handler.
"""

from __future__ import annotations

import logging
import platform
import shlex
import subprocess
import sys

log = logging.getLogger(__name__)


SOUND_FILE_MACOS = "/System/Library/Sounds/Glass.aiff"


def notify_turn_complete(*, subtitle: str = "", session_id: str = "") -> None:
    """Pop a 'Claude finished' banner + play a sound.

    macOS: Uses osascript for banner + afplay for sound
    Windows: Uses PowerShell toast notification + winsound beep
    """
    system = platform.system()
    if system == "Darwin":
        _notify_macos(subtitle, session_id)
    elif system == "Windows":
        _notify_windows(subtitle, session_id)
    else:
        return


def _notify_macos(subtitle: str, session_id: str) -> None:
    """macOS notification via osascript + afplay."""
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
            ["afplay", SOUND_FILE_MACOS],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError) as e:
        log.debug("notify sound failed: %s", e)
    log.debug("notify_turn_complete fired (session=%s)", session_id)


def _notify_windows(subtitle: str, session_id: str) -> None:
    """Windows notification via PowerShell toast + winsound."""
    title = "cc-buddy-bridge"
    body = "Claude finished"
    if subtitle:
        body = f"{subtitle} — {body}"

    # PowerShell toast notification
    ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = @"
<toast>
    <visual>
        <binding template="ToastText02">
            <text id="1">{_escape_xml(title)}</text>
            <text id="2">{_escape_xml(body)}</text>
        </binding>
    </visual>
</toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("cc-buddy-bridge").Show($toast)
"""
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError) as e:
        log.debug("notify toast failed: %s", e)

    # Play system sound
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_OK)
    except (ImportError, RuntimeError) as e:
        log.debug("notify sound failed: %s", e)

    log.debug("notify_turn_complete fired (session=%s)", session_id)


def _q(text: str) -> str:
    """AppleScript single-line string literal — escape backslashes + quotes."""
    safe = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{safe}"'


def _escape_xml(text: str) -> str:
    """Escape XML special characters for toast notification."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;"))
