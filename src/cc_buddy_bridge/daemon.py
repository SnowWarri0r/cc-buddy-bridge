"""Main daemon: wires IPC, BLE, state, and JSONL tailer together."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from .ble import BuddyBLE
from .ipc import IPCServer
from .jsonl_tailer import JSONLTailer
from .matchers import MatcherConfig, classify_command, load_config as load_matcher_config
from .protocol import (
    HEARTBEAT_KEEPALIVE,
    build_heartbeat,
    build_time_sync,
)
from .state import State

log = logging.getLogger(__name__)

# Hook timeout for a permission decision on the stick. REFERENCE.md says the
# desktop app keeps the prompt up indefinitely, but hooks have a finite timeout.
# Default hook timeout is 600s; we cap lower so that a forgotten decision falls
# back to Claude Code's normal approval UI rather than freezing the session.
PERMISSION_WAIT_SECS = 300.0


class Daemon:
    def __init__(
        self,
        socket_path: Optional[str] = None,
        device_name_prefix: str = "Claude",
        device_address: Optional[str] = None,
        matchers: Optional[MatcherConfig] = None,
    ) -> None:
        self.state = State()
        self.ipc = IPCServer(self._handle_ipc, socket_path=socket_path) if socket_path else IPCServer(self._handle_ipc)
        self.ble = BuddyBLE(
            on_message=self._handle_ble,
            name_prefix=device_name_prefix,
            address=device_address,
        )
        self.jsonl = JSONLTailer(self._on_tokens)
        self.matchers = matchers if matchers is not None else load_matcher_config()
        # tool_use_id → Future resolving to "allow" | "deny"
        self._permission_futures: dict[str, asyncio.Future[str]] = {}
        # Track last heartbeat to dedupe (avoid spamming BLE with identical snapshots).
        self._last_hb_serialized: Optional[str] = None
        self._last_hb_sent_at: float = 0.0
        self._shutdown = asyncio.Event()

    # ---- entry ----

    async def run(self) -> None:
        await self.ipc.start()
        tasks = [
            asyncio.create_task(self.ipc.serve_forever(), name="ipc"),
            asyncio.create_task(self.ble.run(), name="ble"),
            asyncio.create_task(self.jsonl.run(), name="jsonl"),
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            asyncio.create_task(self._on_ble_connected(), name="on-connect"),
        ]
        try:
            await self._shutdown.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.ble.stop()
            await self.ipc.stop()

    async def shutdown(self) -> None:
        self._shutdown.set()

    # ---- heartbeat loop ----

    async def _heartbeat_loop(self) -> None:
        while not self._shutdown.is_set():
            await self._push_heartbeat()
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=HEARTBEAT_KEEPALIVE)
            except asyncio.TimeoutError:
                continue

    async def _push_heartbeat(self, force: bool = False) -> None:
        import json

        snap = build_heartbeat(self.state)
        serialized = json.dumps(snap, sort_keys=True, ensure_ascii=False)
        now = time.monotonic()
        changed = serialized != self._last_hb_serialized
        stale = (now - self._last_hb_sent_at) >= HEARTBEAT_KEEPALIVE
        if not (force or changed or stale):
            return
        if self.ble.connected:
            ok = await self.ble.send(snap)
            if ok:
                self._last_hb_serialized = serialized
                self._last_hb_sent_at = now

    async def _on_ble_connected(self) -> None:
        """On every (re)connect, emit time sync + force a heartbeat."""
        while not self._shutdown.is_set():
            await self.ble.wait_connected()
            await self.ble.send(build_time_sync())
            await self._push_heartbeat(force=True)
            # Wait for the connection to drop before waiting again.
            while self.ble.connected and not self._shutdown.is_set():
                await asyncio.sleep(1.0)

    # ---- IPC handler ----

    async def _handle_ipc(self, req: dict[str, Any]) -> dict[str, Any]:
        evt = req.get("evt")
        if evt == "session_start":
            self.state.session_start(
                req["session_id"],
                transcript_path=req.get("transcript_path"),
                cwd=req.get("cwd"),
            )
            await self._push_heartbeat()
            return {"ok": True}

        if evt == "session_end":
            self.state.session_end(req["session_id"])
            await self._push_heartbeat()
            return {"ok": True}

        if evt == "turn_begin":
            self.state.session_start(req["session_id"])  # idempotent
            self.state.turn_begin(req["session_id"])
            prompt = req.get("prompt")
            if isinstance(prompt, str) and prompt:
                self.state.add_entry(f"› {prompt[:60]}")
            await self._push_heartbeat()
            return {"ok": True}

        if evt == "turn_end":
            self.state.turn_end(req["session_id"])
            summary = req.get("summary")
            if isinstance(summary, str) and summary:
                self.state.add_entry(summary[:80])
            await self._push_heartbeat()
            return {"ok": True}

        if evt == "pretooluse":
            return await self._handle_pretooluse(req)

        if evt == "posttooluse":
            # Clear any lingering pending (defensive; normally cleared in _handle_pretooluse).
            self.state.permission_resolved(req.get("tool_use_id", ""))
            tool_name = req.get("tool_name")
            if isinstance(tool_name, str):
                self.state.add_entry(f"✓ {tool_name}")
            await self._push_heartbeat()
            return {"ok": True}

        return {"ok": False, "error": f"unknown evt: {evt!r}"}

    async def _handle_pretooluse(self, req: dict[str, Any]) -> dict[str, Any]:
        tool_use_id = req.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            return {"ok": False, "error": "missing tool_use_id"}
        session_id = req.get("session_id") or "unknown"
        tool_name = req.get("tool_name") or "tool"
        hint = req.get("hint") or ""

        # Smart matcher: classify trivial / risky commands before the BLE round-trip.
        # auto_allow → approve immediately, no stick prompt (keeps ls/cat fast).
        # always_ask → force stick prompt even if Claude Code would auto-approve.
        # default    → no decision, let Claude Code's native permission flow run.
        decision_class = classify_command(hint, self.matchers)
        if decision_class == "allow":
            log.info("pretooluse for %s (%s): auto_allow match → allow", tool_name, hint[:60])
            return {"ok": True, "decision": "allow"}

        # If BLE isn't connected, skip the round-trip and return no decision so
        # Claude Code's normal flow runs (respects user's auto/allow settings).
        if not self.ble.connected:
            log.info("pretooluse for %s: ble not connected, deferring to default flow", tool_name)
            return {"ok": True}

        # Unknown commands don't force a button press — defer to Claude Code's
        # native flow (which may auto-approve under `permissions.defaultMode=auto`).
        # Only always_ask patterns surface on the stick.
        if decision_class == "default":
            log.info("pretooluse for %s (%s): no matcher → defer to default", tool_name, hint[:60])
            return {"ok": True}

        log.info("pretooluse for %s (%s): always_ask → forwarding to stick", tool_name, hint[:60])
        self.state.permission_pending(session_id, tool_use_id, tool_name, hint)
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._permission_futures[tool_use_id] = fut
        try:
            await self._push_heartbeat(force=True)
            try:
                decision = await asyncio.wait_for(fut, timeout=PERMISSION_WAIT_SECS)
            except asyncio.TimeoutError:
                log.warning("permission wait timed out for %s", tool_use_id)
                decision = "ask"
        finally:
            self._permission_futures.pop(tool_use_id, None)
            self.state.permission_resolved(tool_use_id)
            await self._push_heartbeat()
        return {"ok": True, "decision": decision}

    # ---- BLE handler ----

    async def _handle_ble(self, obj: dict[str, Any]) -> None:
        cmd = obj.get("cmd")
        if cmd == "permission":
            tool_use_id = obj.get("id")
            decision = obj.get("decision")
            if decision not in ("once", "deny"):
                log.warning("ignoring permission with unknown decision: %r", obj)
                return
            # Map REFERENCE.md's "once" to Claude Code's "allow".
            mapped = "allow" if decision == "once" else "deny"
            fut = self._permission_futures.get(tool_use_id or "")
            if fut is not None and not fut.done():
                fut.set_result(mapped)
            else:
                log.info("permission %s for unknown id=%s (already resolved?)", decision, tool_use_id)
            return

        if cmd == "status":
            # Device is polling us; ack with a minimal status blob.
            from .protocol import encode  # local import to avoid cycles
            await self.ble.send({"ack": "status", "ok": True, "n": 0})
            return

        if cmd in {"name", "owner", "unpair", "char_begin", "char_end", "file", "file_end", "chunk"}:
            # We're the central; we don't send these, but acknowledge defensively.
            return

        if obj.get("ack") is not None:
            return  # device acknowledging something we sent

        log.debug("ble: unhandled %r", obj)

    # ---- JSONL callback ----

    async def _on_tokens(self, cumulative: int, today: int, _entries: list) -> None:
        self.state.set_tokens(cumulative, today)
        await self._push_heartbeat()
