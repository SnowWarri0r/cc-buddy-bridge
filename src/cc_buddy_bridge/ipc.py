"""IPC between hook scripts and the daemon.

Protocol: line-delimited JSON, one request → one response, then close.

Transport is resolved by ``make_transport``:
* macOS / Linux → Unix domain socket (``/tmp/cc-buddy-bridge.sock``)
* Windows      → TCP loopback (``127.0.0.1:48765``)

Request shapes (``evt`` field discriminates):
  {"evt":"session_start","session_id":"...","transcript_path":"...","cwd":"..."}
  {"evt":"session_end","session_id":"..."}
  {"evt":"turn_begin","session_id":"...","prompt":"..."}
  {"evt":"turn_end","session_id":"...","summary":"..."}
  {"evt":"pretooluse","session_id":"...","tool_use_id":"...","tool_name":"...","hint":"..."}  ← BLOCKS
  {"evt":"posttooluse","session_id":"...","tool_use_id":"..."}

Response shapes:
  {"ok":true}
  {"ok":true,"decision":"allow"|"deny"}  (for pretooluse)
  {"ok":false,"error":"..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol

log = logging.getLogger(__name__)

# Platform defaults. The public DEFAULT_SOCKET_PATH name stays for back-compat
# even though the value is now a transport spec, not always a filesystem path.
DEFAULT_UNIX_PATH = "/tmp/cc-buddy-bridge.sock"
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 48765


ConnHandler = Callable[[Any, Any], Awaitable[None]]


class Transport(Protocol):
    """Abstract local IPC channel. Both async server and sync client sides."""

    @property
    def address(self) -> str:
        """Human-readable address for logs and CLI hints."""
        ...

    async def start_server(self, on_conn: ConnHandler) -> Any:
        """Start an asyncio server bound to this transport."""
        ...

    def sync_connect(self, timeout: float) -> socket.socket:
        """Open a blocking client socket."""
        ...

    def is_in_use(self) -> bool:
        """True iff some other process is actively accepting here."""
        ...

    def cleanup_stale(self) -> None:
        """Remove leftover artifacts, if any."""
        ...


class UnixTransport:
    """AF_UNIX SOCK_STREAM transport for macOS/Linux."""

    def __init__(self, path: str) -> None:
        if sys.platform == "win32":
            raise RuntimeError(
                "UnixTransport is not supported on Windows. "
                "Use a TCP spec like '127.0.0.1:48765' instead."
            )
        self.path = path

    @property
    def address(self) -> str:
        return self.path

    async def start_server(self, on_conn: ConnHandler) -> Any:
        p = Path(self.path)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
        server = await asyncio.start_unix_server(on_conn, path=self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return server

    def sync_connect(self, timeout: float) -> socket.socket:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(self.path)
        return s

    def is_in_use(self) -> bool:
        if not os.path.exists(self.path):
            return False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(self.path)
        except (ConnectionRefusedError, FileNotFoundError):
            self.cleanup_stale()
            return False
        except OSError:
            return True
        else:
            return True
        finally:
            try:
                s.close()
            except OSError:
                pass

    def cleanup_stale(self) -> None:
        try:
            if os.path.exists(self.path):
                os.unlink(self.path)
        except OSError:
            pass


class TcpLoopbackTransport:
    """AF_INET SOCK_STREAM transport restricted to localhost."""

    _ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

    def __init__(self, host: str = DEFAULT_TCP_HOST, port: int = DEFAULT_TCP_PORT) -> None:
        host = host or DEFAULT_TCP_HOST
        if host not in self._ALLOWED_HOSTS:
            raise ValueError(
                f"TCP IPC must bind to loopback only, got {host!r}. "
                f"Use '127.0.0.1:{port}' or ':{port}'."
            )
        self.host = host
        self.port = int(port)

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    async def start_server(self, on_conn: ConnHandler) -> Any:
        return await asyncio.start_server(on_conn, host=self.host, port=self.port)

    def sync_connect(self, timeout: float) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((self.host, self.port))
        return s

    def is_in_use(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect((self.host, self.port))
        except (ConnectionRefusedError, OSError):
            return False
        else:
            return True
        finally:
            try:
                s.close()
            except OSError:
                pass

    def cleanup_stale(self) -> None:
        return None


def parse_spec(spec: str) -> Transport:
    """Parse a local IPC transport spec.

    ``host:port`` and ``:port`` mean TCP loopback. Anything else is a Unix
    socket path, which is unsupported on Windows.
    """
    if spec is None or spec == "":
        raise ValueError("transport spec must be non-empty")

    if ":" in spec:
        host, _sep, port_s = spec.rpartition(":")
        is_drive_letter = (
            len(host) == 1 and host.isalpha() and (port_s.startswith("\\") or port_s.startswith("/"))
        )
        if not is_drive_letter and port_s.isdigit() and "\\" not in spec and "/" not in host:
            port = int(port_s)
            if not (0 < port < 65536):
                raise ValueError(f"port out of range: {port}")
            return TcpLoopbackTransport(host=host or DEFAULT_TCP_HOST, port=port)

    if sys.platform == "win32":
        raise ValueError(
            f"Unix socket paths are not supported on Windows: {spec!r}. "
            f"Use a TCP spec like '127.0.0.1:{DEFAULT_TCP_PORT}' or ':{DEFAULT_TCP_PORT}'."
        )
    return UnixTransport(path=spec)


def default_spec() -> str:
    """Platform-appropriate default transport spec."""
    if sys.platform == "win32":
        return f"{DEFAULT_TCP_HOST}:{DEFAULT_TCP_PORT}"
    return DEFAULT_UNIX_PATH


def make_transport(spec: Optional[str] = None) -> Transport:
    """Resolve a Transport from explicit CLI spec, env var, or platform default."""
    chosen = spec or os.environ.get("CC_BUDDY_BRIDGE_SOCK") or default_spec()
    return parse_spec(chosen)


DEFAULT_SOCKET_PATH = default_spec()


# Handler signature: async (request_dict) -> response_dict.
Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class IPCServer:
    """Listens on a transport, dispatches one request per connection.

    ``socket_path`` is a transport spec (path or ``host:port``). Name kept for
    back-compat; ``Daemon`` passes it through unmodified.
    """

    def __init__(self, handler: Handler, socket_path: Optional[str] = None) -> None:
        self.handler = handler
        self._transport: Transport = make_transport(socket_path)
        self._server: asyncio.AbstractServer | None = None

    @property
    def address(self) -> str:
        """Transport-specific address string for logs / errors."""
        return self._transport.address

    # Back-compat alias: existing callers / tests may still read .socket_path.
    @property
    def socket_path(self) -> str:
        return self._transport.address

    async def start(self) -> None:
        self._server = await self._transport.start_server(self._on_conn)
        log.info("ipc listening at %s", self._transport.address)

    async def serve_forever(self) -> None:
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._transport.cleanup_stale()

    async def _on_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            try:
                req = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                await self._reply(writer, {"ok": False, "error": f"bad json: {e}"})
                return
            try:
                resp = await self.handler(req)
            except Exception as e:  # noqa: BLE001 — handler faults shouldn't kill the server
                log.exception("handler error for req=%r", req)
                resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            await self._reply(writer, resp)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, obj: dict[str, Any]) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8", errors="replace")
        writer.write(data)
        await writer.drain()
