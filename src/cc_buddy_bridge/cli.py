"""Entry point. `cc-buddy-bridge [daemon|install|uninstall|status]`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
import sys

from . import __version__
from .daemon import Daemon
from .ipc import DEFAULT_SOCKET_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cc-buddy-bridge")
    parser.add_argument("--version", action="version", version=f"cc-buddy-bridge {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p_daemon = sub.add_parser("daemon", help="Run the bridge daemon (connects to BLE device, serves hooks)")
    p_daemon.add_argument("--socket", default=None, help="Unix socket path (default /tmp/cc-buddy-bridge.sock)")
    p_daemon.add_argument("--device-name", default="Claude", help="BLE name prefix to match (default: Claude)")
    p_daemon.add_argument("--device-address", default=None, help="BLE address to connect to (skips scan)")
    p_daemon.add_argument("--log-level", default="INFO")

    p_install = sub.add_parser("install", help="Register hooks in ~/.claude/settings.json")
    p_install.add_argument(
        "--service", action="store_true",
        help="Install as a macOS launchd agent (daemon auto-starts on login) instead of registering hooks",
    )
    p_uninstall = sub.add_parser("uninstall", help="Remove cc-buddy-bridge hooks from ~/.claude/settings.json")
    p_uninstall.add_argument(
        "--service", action="store_true",
        help="Remove the macOS launchd agent instead of removing hooks",
    )
    sub.add_parser("status", help="Show install status")

    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 1

    if args.cmd == "daemon":
        return _run_daemon(args)
    if args.cmd == "install":
        if getattr(args, "service", False):
            from .service import install_service
            return install_service()
        from .installer import install_hooks
        return install_hooks()
    if args.cmd == "uninstall":
        if getattr(args, "service", False):
            from .service import uninstall_service
            return uninstall_service()
        from .installer import uninstall_hooks
        return uninstall_hooks()
    if args.cmd == "status":
        from .installer import show_status
        return show_status()

    return 1


def _run_daemon(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Refuse to start if another daemon is already listening on this socket.
    # A stale socket (file exists but nobody is accepting) is safe to remove
    # and proceed. This prevents last night's "two daemons competing for the
    # BLE connection" footgun.
    socket_path = args.socket or DEFAULT_SOCKET_PATH
    if _socket_in_use(socket_path):
        print(
            f"cc-buddy-bridge: another daemon is already listening at {socket_path}.\n"
            f"  Stop it first, or pass --socket to use a different path.",
            file=sys.stderr,
        )
        return 2

    daemon = Daemon(
        socket_path=args.socket,
        device_name_prefix=args.device_name,
        device_address=args.device_address,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _sigterm(*_: object) -> None:
        asyncio.ensure_future(daemon.shutdown(), loop=loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sigterm)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(daemon.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
    return 0


def _socket_in_use(path: str) -> bool:
    """True iff a process is actively accepting on ``path``.

    A Unix socket file left over from a crash returns ECONNREFUSED on connect;
    we remove the stale file and return False so the new daemon can bind.
    """
    if not os.path.exists(path):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(path)
    except (ConnectionRefusedError, FileNotFoundError):
        # Stale socket file — clean up and proceed.
        try:
            os.unlink(path)
        except OSError:
            pass
        return False
    except OSError:
        # Some other error (permissions, socket unreadable). Be conservative
        # and treat as in-use so we don't clobber something.
        return True
    else:
        return True
    finally:
        try:
            s.close()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
