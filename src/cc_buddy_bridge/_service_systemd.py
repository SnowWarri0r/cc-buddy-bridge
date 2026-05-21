"""Linux systemd user-unit backend for the daemon auto-start service.

Writes a user-level unit at ``~/.config/systemd/user/<UNIT_NAME>`` that
runs ``cc-buddy-bridge daemon`` and keeps it alive across crashes. Stdout
and stderr go through systemd-journald — view them with
``journalctl --user -u cc-buddy-bridge.service``.

By default a user manager exits when the user logs out, so the daemon
stops with it. Enable lingering (``loginctl enable-linger $USER``) if you
want the daemon to survive logout / start at boot.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

NAME = "systemd"
UNIT_NAME = "cc-buddy-bridge.service"
UNIT_PATH = Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def _build_unit() -> str:
    """Render the .service unit file.

    ``ExecStart`` uses the Python interpreter that's running *this* install
    command — same trick as the macOS plist — so the unit picks up the venv
    that has bleak and watchfiles installed.
    """
    return (
        "[Unit]\n"
        "Description=Claude Code <-> desktop-buddy BLE bridge\n"
        "After=default.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={sys.executable} -m cc_buddy_bridge.cli daemon\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True,
    )


def install() -> int:
    if shutil.which("systemctl") is None:
        print("cc-buddy-bridge: `systemctl` not found on PATH", file=sys.stderr)
        return 2

    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIT_PATH.write_text(_build_unit(), encoding="utf-8")

    reload_res = _systemctl("daemon-reload")
    if reload_res.returncode != 0:
        print(
            f"systemctl --user daemon-reload failed ({reload_res.returncode}): "
            f"{reload_res.stderr.strip()}",
            file=sys.stderr,
        )
        return 2

    enable_res = _systemctl("enable", "--now", UNIT_NAME)
    if enable_res.returncode != 0:
        print(
            f"systemctl --user enable --now failed ({enable_res.returncode}): "
            f"{enable_res.stderr.strip()}",
            file=sys.stderr,
        )
        return 2

    print(f"installed: {UNIT_PATH}")
    print(f"logs:      journalctl --user -u {UNIT_NAME}")
    print("daemon is running and will start on your next login.")
    print(
        "tip: run `loginctl enable-linger $USER` if you want it to survive "
        "logout / start at boot."
    )
    return 0


def uninstall() -> int:
    if not UNIT_PATH.exists():
        print("service not installed; nothing to do")
        return 0

    if shutil.which("systemctl") is not None:
        _systemctl("disable", "--now", UNIT_NAME)
    UNIT_PATH.unlink()
    if shutil.which("systemctl") is not None:
        _systemctl("daemon-reload")
    print(f"removed: {UNIT_PATH}")
    return 0


def is_installed() -> bool:
    return UNIT_PATH.exists()


def is_loaded() -> bool:
    """True iff systemd reports the unit currently active.

    ``is-active`` exits 0 when the unit is active, non-zero otherwise.
    """
    if shutil.which("systemctl") is None:
        return False
    result = _systemctl("is-active", "--quiet", UNIT_NAME)
    return result.returncode == 0


def unit_path() -> Path:
    return UNIT_PATH


def log_path() -> str:
    return f"journalctl --user -u {UNIT_NAME}"
