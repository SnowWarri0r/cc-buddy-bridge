"""Windows Task Scheduler backend for daemon auto-start."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

NAME = "task-scheduler"
TASK_NAME = "cc-buddy-bridge-daemon"
LOG_PATH = Path.home() / "AppData" / "Local" / "cc-buddy-bridge" / "daemon.log"


def install() -> int:
    if shutil.which("schtasks") is None:
        print("cc-buddy-bridge: `schtasks` not found on PATH", file=sys.stderr)
        return 2

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    cmd = f'"{sys.executable}" -m cc_buddy_bridge.cli daemon >> "{LOG_PATH}" 2>&1'

    result = subprocess.run(
        [
            "schtasks", "/create",
            "/tn", TASK_NAME,
            "/tr", cmd,
            "/sc", "onlogon",
            "/f",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"schtasks create failed ({result.returncode}): {result.stderr.strip()}",
              file=sys.stderr)
        return 2

    print(f"installed: Task Scheduler task '{TASK_NAME}'")
    print(f"logs at:   {LOG_PATH}")
    print("daemon will start on your next login.")
    print("To start now, run: cc-buddy-bridge daemon")
    return 0


def uninstall() -> int:
    if not is_installed():
        print("service not installed; nothing to do")
        return 0

    result = subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"schtasks delete failed ({result.returncode}): {result.stderr.strip()}",
              file=sys.stderr)
        return 2

    print(f"removed: Task Scheduler task '{TASK_NAME}'")
    return 0


def is_installed() -> bool:
    if shutil.which("schtasks") is None:
        return False
    result = subprocess.run(
        ["schtasks", "/query", "/tn", TASK_NAME],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def is_loaded() -> bool:
    return is_installed()


def unit_path() -> str:
    return f"Task Scheduler: {TASK_NAME}"


def log_path() -> Path:
    return LOG_PATH
