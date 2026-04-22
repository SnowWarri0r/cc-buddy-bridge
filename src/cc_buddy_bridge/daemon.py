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
    build_turn_event,
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
        self.jsonl = JSONLTailer(self._on_tokens, on_assistant_text=self._on_assistant_text)
        self.matchers = matchers if matchers is not None else load_matcher_config()
        # tool_use_id → Future resolving to "allow" | "deny"
        self._permission_futures: dict[str, asyncio.Future[str]] = {}
        # transcript_path → hash of the last assistant content we emitted as an
        # entry. Used to distinguish "fresh turn" from "re-read old content"
        # when the transcript file hasn't been flushed yet.
        self._last_emitted_turn_key: dict[str, str] = {}
        # session_id → task that'll flip running→0 after a grace window.
        # Delays the turn_end so the stick's HUD stays drawn long enough to
        # display the @-entry the tailer just emitted. See firmware's
        # drawHUD/clocking gate in main.cpp.
        self._pending_turn_ends: dict[str, asyncio.Task] = {}
        # Cached stick-side status fields from the most recent status ack.
        self._last_stick_sec: Optional[bool] = None
        self._last_stick_battery_pct: Optional[int] = None
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
            asyncio.create_task(self._status_poller(), name="status-poller"),
        ]
        try:
            await self._shutdown.wait()
        finally:
            for t in tasks:
                t.cancel()
            for pend in list(self._pending_turn_ends.values()):
                if not pend.done():
                    pend.cancel()
            await asyncio.gather(*tasks, *self._pending_turn_ends.values(),
                                 return_exceptions=True)
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
            log.debug(
                "heartbeat: %d bytes, entries=%d (last=%r), force=%s, changed=%s",
                len(serialized), len(snap.get("entries", [])),
                snap["entries"][-1] if snap.get("entries") else None,
                force, changed,
            )
            ok = await self.ble.send(snap)
            if ok:
                self._last_hb_serialized = serialized
                self._last_hb_sent_at = now
            else:
                log.warning("heartbeat: ble.send returned failure")

    async def _on_ble_connected(self) -> None:
        """On every (re)connect, emit time sync + force a heartbeat + kick
        a status poll so we learn the link's encryption state right away."""
        while not self._shutdown.is_set():
            await self.ble.wait_connected()
            await self.ble.send(build_time_sync())
            await self._push_heartbeat(force=True)
            await self.ble.send({"cmd": "status"})
            # Wait for the connection to drop before waiting again.
            while self.ble.connected and not self._shutdown.is_set():
                await asyncio.sleep(1.0)

    async def _status_poller(self) -> None:
        """Periodically ask the stick for its status so we track sec/battery.
        Ack handling lives in _handle_ble (ack:"status" branch)."""
        POLL_INTERVAL = 60.0
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=POLL_INTERVAL)
                return
            except asyncio.TimeoutError:
                pass
            if self.ble.connected:
                await self.ble.send({"cmd": "status"})

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
            session_id = req["session_id"]
            # A new user prompt cancels any pending deferred turn_end.
            pending = self._pending_turn_ends.pop(session_id, None)
            if pending is not None and not pending.done():
                pending.cancel()
            self.state.session_start(session_id)  # idempotent
            self.state.turn_begin(session_id)
            prompt = req.get("prompt")
            if isinstance(prompt, str) and prompt:
                self.state.add_entry(f"> {prompt[:60]}")
            await self._push_heartbeat()
            return {"ok": True}

        if evt == "turn_end":
            # Don't flip running→0 immediately; the firmware enters clock mode
            # as soon as running+waiting both hit zero, which blanks the
            # transcript HUD before the user has a chance to read the entry
            # we just added. Schedule the flip 15s out — long enough to read,
            # short enough that idle really does clock. A new turn_begin
            # cancels the scheduled task.
            session_id = req["session_id"]
            previous = self._pending_turn_ends.get(session_id)
            if previous is not None and not previous.done():
                previous.cancel()
            self._pending_turn_ends[session_id] = asyncio.create_task(
                self._deferred_turn_end(session_id, delay=15.0)
            )
            return {"ok": True}

        if evt == "pretooluse":
            return await self._handle_pretooluse(req)

        if evt == "posttooluse":
            # Clear any lingering pending (defensive; normally cleared in _handle_pretooluse).
            self.state.permission_resolved(req.get("tool_use_id", ""))
            tool_name = req.get("tool_name")
            if isinstance(tool_name, str):
                self.state.add_entry(f"+ {tool_name}")
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

        log.info(
            "permission request: tool=%s id=%s hint=%r waiting up to %.0fs",
            tool_name, tool_use_id, hint[:80], PERMISSION_WAIT_SECS,
        )
        pending = self.state.permission_pending(session_id, tool_use_id, tool_name, hint)
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._permission_futures[tool_use_id] = fut
        try:
            await self._push_heartbeat(force=True)
            try:
                decision = await asyncio.wait_for(fut, timeout=PERMISSION_WAIT_SECS)
                elapsed = time.monotonic() - pending.issued_at
                log.info(
                    "permission resolved: id=%s decision=%s (%.1fs)",
                    tool_use_id, decision, elapsed,
                )
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - pending.issued_at
                log.warning(
                    "permission timeout: id=%s tool=%s after %.1fs → falling back to 'ask'",
                    tool_use_id, tool_name, elapsed,
                )
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
                log.info(
                    "permission button press: id=%s → %s (stick sent %r)",
                    tool_use_id, mapped, decision,
                )
                fut.set_result(mapped)
            else:
                log.info(
                    "permission %s received for id=%s but no pending request (timed out or already resolved)",
                    decision, tool_use_id,
                )
            return

        # Status acks come back from the device after we poll with {"cmd":"status"}.
        # Shape per REFERENCE.md: {"ack":"status","ok":true,"data":{"name","sec","bat":{...},"sys":{...},"stats":{...}}}.
        ack = obj.get("ack")
        if ack == "status" and obj.get("ok"):
            data = obj.get("data") or {}
            sec = data.get("sec")
            if sec is not None and sec != self._last_stick_sec:
                log.info(
                    "stick link: %s (was %s)",
                    "ENCRYPTED" if sec else "UNENCRYPTED — transcript sniffable!",
                    self._last_stick_sec,
                )
                self._last_stick_sec = bool(sec)
            bat = data.get("bat") or {}
            if isinstance(bat, dict) and bat:
                pct = bat.get("pct")
                ma = bat.get("mA")
                if isinstance(pct, int) and pct != self._last_stick_battery_pct:
                    charging = "+" if isinstance(ma, int) and ma < 0 else " "
                    log.info("stick battery: %d%% %s", pct, charging)
                    self._last_stick_battery_pct = pct
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

    async def _on_assistant_text(self, _transcript_path: str, text: str, _uuid: str) -> None:
        """Fired by the JSONL tailer the moment a new assistant text record
        lands on disk (typically <500 ms after Claude Code finishes the
        message). Emitting here beats the Stop hook, so the stick receives
        the '@ ...' entry while the user is still looking at the terminal —
        before auto-off kicks in."""
        self.state.add_entry(f"@ {text[:70]}")
        log.info("tailer: new assistant text → entry added (state.entries=%d)",
                 len(self.state.entries))
        await self._push_heartbeat(force=True)

    async def _deferred_turn_end(self, session_id: str, delay: float) -> None:
        """Delay the state.turn_end so that running>0 keeps the firmware out of
        clock mode long enough to render the @-entry the tailer just pushed."""
        try:
            await asyncio.sleep(delay)
            self.state.turn_end(session_id)
            await self._push_heartbeat()
        except asyncio.CancelledError:
            return
        finally:
            # Self-cleanup. If a new turn_begin already replaced the task, the
            # pop returns that task instead — ignore the mismatch.
            current = self._pending_turn_ends.get(session_id)
            if current is not None and current.done():
                self._pending_turn_ends.pop(session_id, None)

    # ---- turn event ----

    async def _emit_turn_event(self, transcript_path: str) -> None:
        """On turn_end: mirror the latest assistant text into the heartbeat's
        ``entries`` list so the stick's transcript view shows it.

        The reference firmware silently drops {"evt":"turn"} events (its JSON
        parser only reads heartbeat fields), so the only thing that actually
        shows up for the user is the synthetic entry we add below.

        Polls for fresh content: Claude Code flushes assistant records to the
        transcript JSONL *after* the Stop hook fires, so a naive read grabs
        the PREVIOUS turn's content. We hash what we read and compare to the
        last content we emitted; if unchanged, wait 200 ms and retry, up to
        ~1.2 s total before giving up.
        """
        if not self.ble.connected:
            return

        # Claude Code's transcript writes are async w.r.t. the Stop hook — the
        # hook fires before the final assistant record hits disk. Sleep a beat
        # so our first read sees the just-finished turn; then poll for up to
        # another ~1.2s if that wasn't enough (e.g., long response still being
        # serialized). Dedupe by content hash so we never re-emit the same turn.
        await asyncio.sleep(1.0)

        import hashlib
        import json as _json
        last_key = self._last_emitted_turn_key.get(transcript_path)
        content: list | None = None
        content_key: str | None = None
        for attempt in range(6):
            if attempt > 0:
                await asyncio.sleep(0.2)
            try:
                self.jsonl._process_file(transcript_path)
            except Exception:  # noqa: BLE001
                log.debug("turn event: process_file failed", exc_info=True)
            candidate = self.jsonl.last_assistant_content(transcript_path)
            if not candidate:
                continue
            key = hashlib.md5(
                _json.dumps(candidate, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            if key != last_key:
                content = candidate
                content_key = key
                break
            log.debug("turn event: transcript content unchanged, retrying (attempt %d)", attempt + 1)

        if not content:
            log.info("turn end: no fresh content after 1s warmup + 1.2s polling")
            return

        text = _first_text_block(content)
        if text:
            log.info("turn end: adding entry '@ %s...' (state.entries len before=%d)",
                     text[:30], len(self.state.entries))
            self.state.add_entry(f"@ {text[:70]}")
            await self._push_heartbeat(force=True)
            if content_key is not None:
                self._last_emitted_turn_key[transcript_path] = content_key
        else:
            log.info("turn end: content found but no text block, skipping entry add")


def _first_text_block(content: list) -> str:
    """Pull the first text block out of an SDK content array. Returns '' if
    the turn was purely tool_use / tool_result (no natural-language reply)."""
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""
