"""Daemon auto-start service: install/uninstall a user-level unit.

Dispatches to a platform-specific backend:

* ``darwin`` → launchd user agent (``_service_launchd``)
* ``linux``  → systemd user unit  (``_service_systemd``)

Other platforms aren't supported; the install/uninstall calls return a
non-zero exit code with a helpful message instead.

The public surface (``install_service``, ``uninstall_service``,
``is_installed``, ``is_loaded``, ``unit_path``, ``log_path``,
``backend_name``) is platform-agnostic so callers (CLI, status output)
don't need to branch on ``sys.platform``.
"""

from __future__ import annotations

import sys
from typing import Any


def _backend() -> Any | None:
    if sys.platform == "darwin":
        from . import _service_launchd
        return _service_launchd
    if sys.platform.startswith("linux"):
        from . import _service_systemd
        return _service_systemd
    return None


def _unsupported_platform_msg() -> str:
    return (
        f"cc-buddy-bridge: service install is only supported on macOS and Linux "
        f"(got {sys.platform!r})."
    )


def install_service() -> int:
    backend = _backend()
    if backend is None:
        print(_unsupported_platform_msg(), file=sys.stderr)
        return 2
    return backend.install()


def uninstall_service() -> int:
    backend = _backend()
    if backend is None:
        print(_unsupported_platform_msg(), file=sys.stderr)
        return 2
    return backend.uninstall()


def is_installed() -> bool:
    backend = _backend()
    return backend is not None and backend.is_installed()


def is_loaded() -> bool:
    backend = _backend()
    return backend is not None and backend.is_loaded()


def backend_name() -> str | None:
    """Human-readable backend identifier ("launchd", "systemd"), or None."""
    backend = _backend()
    return backend.NAME if backend is not None else None


def unit_path() -> Any | None:
    """Path to the installed unit file, or None on unsupported platforms."""
    backend = _backend()
    return backend.unit_path() if backend is not None else None


def log_path() -> Any | None:
    """Where to look for daemon logs.

    Returns a ``Path`` on macOS (a real log file) and a string on Linux
    (the ``journalctl`` invocation, since journald is the log store).
    """
    backend = _backend()
    return backend.log_path() if backend is not None else None
