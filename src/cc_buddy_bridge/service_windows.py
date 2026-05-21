"""Windows Task Scheduler install/uninstall helpers."""

from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from pathlib import Path

from .logging_setup import log_path as _project_log_path

LABEL = "com.github.cc-buddy-bridge.daemon"
SERVICE_KIND = "Windows Task Scheduler"
TASK_NAME = LABEL


def log_path() -> Path:
    return _project_log_path()


def definition_location() -> str:
    return TASK_NAME


def _pythonw_executable() -> str:
    python = Path(sys.executable)
    candidate = python.with_name("pythonw.exe")
    if candidate.exists():
        return str(candidate)
    return str(python)


def _task_command() -> str:
    return subprocess.list2cmdline([_pythonw_executable(), "-m", "cc_buddy_bridge.cli", "daemon"])


def _task_user() -> str:
    return getpass.getuser()


def install_service() -> int:
    if sys.platform != "win32":
        print("cc-buddy-bridge: service install is Windows-only for this backend.", file=sys.stderr)
        return 2

    if shutil.which("schtasks") is None:
        print("cc-buddy-bridge: `schtasks` not found on PATH", file=sys.stderr)
        return 2

    current_log_path = log_path()
    current_log_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/SC",
            "ONLOGON",
            "/TN",
            TASK_NAME,
            "/TR",
            _task_command(),
            "/RU",
            _task_user(),
            "/IT",
            "/NP",
            "/RL",
            "LIMITED",
            "/F",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        print(f"schtasks /Create failed ({result.returncode}): {detail}", file=sys.stderr)
        return 2

    print(f"installed scheduled task: {TASK_NAME}")
    print(f"logs at:                {current_log_path}")
    print("daemon will start on your next logon.")
    return 0


def uninstall_service() -> int:
    if sys.platform != "win32":
        print("cc-buddy-bridge: service uninstall is Windows-only for this backend", file=sys.stderr)
        return 2

    if not is_installed():
        print("service not installed; nothing to do")
        return 0

    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        print(f"schtasks /Delete failed ({result.returncode}): {detail}", file=sys.stderr)
        return 2

    print(f"removed scheduled task: {TASK_NAME}")
    return 0


def is_installed() -> bool:
    if sys.platform != "win32" or shutil.which("schtasks") is None:
        return False
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def is_loaded() -> bool:
    return is_installed()


def status_summary() -> str:
    return "scheduled at logon"
