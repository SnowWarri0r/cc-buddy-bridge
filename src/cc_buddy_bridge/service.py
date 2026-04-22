"""macOS launchd agent install/uninstall.

Writes a user-level LaunchAgent at ``~/Library/LaunchAgents/<LABEL>.plist``
that runs ``cc-buddy-bridge daemon`` at login and keeps it alive across
crashes. Logs stdout/stderr to ``~/Library/Logs/cc-buddy-bridge.log``.

Only macOS is supported for now; a linux systemd user-unit variant is
tracked in issue #4.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.github.cc-buddy-bridge.daemon"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library" / "Logs" / "cc-buddy-bridge.log"


def _build_plist() -> bytes:
    """Render the plist as XML bytes.

    ``ProgramArguments`` uses the Python interpreter that's running *this*
    install command, so a user who installs from inside the project venv
    gets a service pointing at that venv's python — which has bleak and
    watchfiles installed. No need for a separate executable path.

    ``ProcessType = Interactive`` tells launchd this agent runs in the user's
    GUI session; required for CoreBluetooth (BLE) access on macOS.
    """
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "cc_buddy_bridge.cli", "daemon"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            # Keep a reasonable default PATH — launchd starts with an empty
            # one, and some bleak paths shell out.
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }
    return plistlib.dumps(plist)


def install_service() -> int:
    if sys.platform != "darwin":
        print(
            "cc-buddy-bridge: service install is macOS-only for now.\n"
            "  Linux users: see issue #4 for the systemd variant.",
            file=sys.stderr,
        )
        return 2

    if shutil.which("launchctl") is None:
        print("cc-buddy-bridge: `launchctl` not found on PATH", file=sys.stderr)
        return 2

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(_build_plist())

    # Unload first so idempotent re-install picks up any new interpreter path.
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    result = subprocess.run(
        ["launchctl", "load", "-w", str(PLIST_PATH)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"launchctl load failed ({result.returncode}): {result.stderr.strip()}",
              file=sys.stderr)
        return 2

    print(f"installed: {PLIST_PATH}")
    print(f"logs at:   {LOG_PATH}")
    print("daemon will start on your next login (and is starting now).")
    return 0


def uninstall_service() -> int:
    if sys.platform != "darwin":
        print("cc-buddy-bridge: service uninstall is macOS-only", file=sys.stderr)
        return 2

    if not PLIST_PATH.exists():
        print("service not installed; nothing to do")
        return 0

    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    PLIST_PATH.unlink()
    print(f"removed: {PLIST_PATH}")
    return 0


def is_installed() -> bool:
    return sys.platform == "darwin" and PLIST_PATH.exists()


def is_loaded() -> bool:
    """True iff launchctl reports the agent currently loaded."""
    if sys.platform != "darwin" or shutil.which("launchctl") is None:
        return False
    result = subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False
    return any(LABEL in line for line in result.stdout.splitlines())
