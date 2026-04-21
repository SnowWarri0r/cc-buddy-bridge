"""Unix-socket IPC between hook scripts and the daemon.

Protocol: line-delimited JSON, one request → one response, then close.

Request shapes (`evt` field discriminates):
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
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

DEFAULT_SOCKET_PATH = os.environ.get(
    "CC_BUDDY_BRIDGE_SOCK",
    "/tmp/cc-buddy-bridge.sock",
)


# Handler signature: async (request_dict) -> response_dict.
Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class IPCServer:
    def __init__(self, handler: Handler, socket_path: str = DEFAULT_SOCKET_PATH) -> None:
        self.handler = handler
        self.socket_path = socket_path
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        # Remove stale socket from a previous run.
        p = Path(self.socket_path)
        if p.exists():
            p.unlink()
        self._server = await asyncio.start_unix_server(self._on_conn, path=self.socket_path)
        os.chmod(self.socket_path, 0o600)  # user-only
        log.info("ipc listening at %s", self.socket_path)

    async def serve_forever(self) -> None:
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        p = Path(self.socket_path)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    async def _on_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
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
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        writer.write(data)
        await writer.drain()
