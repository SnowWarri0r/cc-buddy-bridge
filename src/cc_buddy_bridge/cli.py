"""Entry point. `cc-buddy-bridge [daemon|install|uninstall|status]`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from . import __version__
from .daemon import Daemon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cc-buddy-bridge")
    parser.add_argument("--version", action="version", version=f"cc-buddy-bridge {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p_daemon = sub.add_parser("daemon", help="Run the bridge daemon (connects to BLE device, serves hooks)")
    p_daemon.add_argument("--socket", default=None, help="Unix socket path (default /tmp/cc-buddy-bridge.sock)")
    p_daemon.add_argument("--device-name", default="Claude", help="BLE name prefix to match (default: Claude)")
    p_daemon.add_argument("--device-address", default=None, help="BLE address to connect to (skips scan)")
    p_daemon.add_argument("--log-level", default="INFO")

    sub.add_parser("install", help="Register hooks in ~/.claude/settings.json")
    sub.add_parser("uninstall", help="Remove cc-buddy-bridge hooks from ~/.claude/settings.json")
    sub.add_parser("status", help="Show install status")

    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 1

    if args.cmd == "daemon":
        return _run_daemon(args)
    if args.cmd == "install":
        from .installer import install_hooks
        return install_hooks()
    if args.cmd == "uninstall":
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


if __name__ == "__main__":
    sys.exit(main())
