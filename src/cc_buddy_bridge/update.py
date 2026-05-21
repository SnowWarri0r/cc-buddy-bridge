"""Self-update via ``git pull && pip install -e .`` + service restart.

Designed for the common case: cloned from GitHub, running in an editable
venv, daemon managed by the platform's service backend (launchd / systemd
user unit / Task Scheduler). Bails clearly on:

- Not a git checkout (e.g. installed from a wheel) — direct user to pip
- Uncommitted local changes — direct user to stash/commit first
- Already on latest — no-op
- Non-tty without --yes — refuse to prompt blindly

We *do not* try to be cleverer than git here. If the user is on a feature
branch, mid-rebase, or has a dirty tree, we say so and step away.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .version_check import check

log = logging.getLogger(__name__)


def package_repo_root() -> Optional[Path]:
    """Walk up from this module to find a ``.git`` dir. None if installed
    from a wheel (PyPI / built package), which means self-update isn't
    available — user should use pip directly."""
    here = Path(__file__).resolve().parent
    for ancestor in [here, *here.parents]:
        if (ancestor / ".git").exists():
            return ancestor
    return None


def _run(cmd: list[str], *, cwd: Optional[Path] = None) -> tuple[int, str, str]:
    """subprocess.run wrapper that returns (rc, stdout, stderr) instead of
    raising. Streams nothing — caller decides what to print."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        return 127, "", str(e)
    return result.returncode, result.stdout, result.stderr


def _git_status_clean(repo: Path) -> bool:
    rc, out, _ = _run(["git", "status", "--porcelain"], cwd=repo)
    return rc == 0 and not out.strip()


def _current_branch(repo: Path) -> str:
    rc, out, _ = _run(["git", "branch", "--show-current"], cwd=repo)
    return out.strip() if rc == 0 else ""


def _detect_service_backend() -> Optional[str]:
    """Best-effort guess at what to restart after the install.

    We only restart when the service is plausibly installed; otherwise we tell
    the user to bounce the daemon themselves.
    """
    if sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.github.cc-buddy-bridge.daemon.plist"
        return "launchd" if plist.exists() else None
    if sys.platform == "win32":
        # schtasks query is the most reliable check; defer to subprocess.
        rc, _, _ = _run(["schtasks", "/query", "/tn", "cc-buddy-bridge-daemon"])
        return "scheduler" if rc == 0 else None
    # Linux / other Unix — assume systemd user unit if the file is there.
    unit = Path.home() / ".config" / "systemd" / "user" / "cc-buddy-bridge.service"
    return "systemd" if unit.exists() else None


def _restart_service(backend: str) -> tuple[bool, str]:
    """Restart the daemon. Returns (ok, human-readable message)."""
    if backend == "launchd":
        uid = os.getuid()
        rc, _, err = _run([
            "launchctl", "kickstart", "-k",
            f"gui/{uid}/com.github.cc-buddy-bridge.daemon",
        ])
        if rc == 0:
            return True, "Daemon restarted (launchctl kickstart)."
        return False, f"launchctl failed: {err.strip() or 'unknown error'}"
    if backend == "systemd":
        rc, _, err = _run(["systemctl", "--user", "restart", "cc-buddy-bridge.service"])
        if rc == 0:
            return True, "Daemon restarted (systemctl --user)."
        return False, f"systemctl failed: {err.strip() or 'unknown error'}"
    if backend == "scheduler":
        # Stop + start to force a re-run with the new code.
        _run(["schtasks", "/end", "/tn", "cc-buddy-bridge-daemon"])
        rc, _, err = _run(["schtasks", "/run", "/tn", "cc-buddy-bridge-daemon"])
        if rc == 0:
            return True, "Daemon restarted (Task Scheduler)."
        return False, f"schtasks failed: {err.strip() or 'unknown error'}"
    return False, f"Unknown service backend: {backend!r}"


def run_update(*, yes: bool = False) -> int:
    """Entry point for ``cc-buddy-bridge update``. Returns process exit code."""
    info = check(force=True)
    if not info.has_update:
        if info.latest is None:
            print("Could not reach github.com to check for updates. Try again later.")
            return 2
        print(f"Already on the latest release ({info.current}).")
        return 0

    repo = package_repo_root()
    if repo is None:
        print(
            "cc-buddy-bridge: this install isn't a git checkout — self-update "
            "isn't supported.\n"
            "Upgrade with `pip install -U cc-buddy-bridge` (when on PyPI) or "
            "re-clone the repo.",
            file=sys.stderr,
        )
        return 2

    if not _git_status_clean(repo):
        print(
            f"cc-buddy-bridge: uncommitted changes in {repo} — refusing to "
            "`git pull`.\n"
            "Stash or commit them first, then retry.",
            file=sys.stderr,
        )
        return 2

    branch = _current_branch(repo) or "(detached)"
    backend = _detect_service_backend()

    print(f"Update plan for {repo}:")
    print(f"  Current:  {info.current}  ({branch})")
    print(f"  Target:   {info.latest}")
    print("  Steps:    git pull → pip install -e .", end="")
    print(f"  →  restart via {backend}" if backend else "  →  (no service backend detected)")

    if not yes:
        if not sys.stdin.isatty():
            print(
                "cc-buddy-bridge: stdin is not a terminal — re-run with `-y` "
                "to skip the confirmation prompt.",
                file=sys.stderr,
            )
            return 2
        try:
            confirm = input("\nProceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return 1

    print()
    print("$ git pull")
    rc, _, err = _run(["git", "pull"], cwd=repo)
    if rc != 0:
        print(f"git pull failed: {err.strip() or 'unknown'}", file=sys.stderr)
        return 2

    print(f"$ {sys.executable} -m pip install -e .")
    rc, _, err = _run([sys.executable, "-m", "pip", "install", "-e", "."], cwd=repo)
    if rc != 0:
        print(f"pip install failed: {err.strip() or 'unknown'}", file=sys.stderr)
        print("Repo is updated; only the install step failed. Re-run pip "
              "install manually.", file=sys.stderr)
        return 2

    if backend is None:
        print("\nDone. Restart your daemon manually to pick up the new code.")
        return 0

    ok, msg = _restart_service(backend)
    print(f"\n{msg}")
    return 0 if ok else 2
