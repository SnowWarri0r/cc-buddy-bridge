"""BLE client that pairs with a claude-desktop-buddy device over Nordic UART Service.

Scans for peripherals whose advertised local name starts with "Claude", connects,
subscribes to TX notifications, and exposes an async `send()` method that writes
newline-terminated JSON to RX.

Uses bleak. macOS passes a CoreBluetooth-assigned UUID instead of a MAC address,
so the scan result is cached under the device's advertised name.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any, Awaitable, Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from .protocol import (
    NUS_RX_UUID,
    NUS_TX_UUID,
    LineAssembler,
    encode,
)

log = logging.getLogger(__name__)

# Default scan parameters.
DEFAULT_NAME_PREFIX = "Claude"
# Windows uses a short 3s scan: the radio-reset recovery (below) keys its
# threshold timing off fast scan cycles, and BLE adverts arrive every
# 100-500ms so 3s is ample to catch a present device. Other platforms keep the
# original 10s for more discovery margin in congested 2.4GHz environments or
# with long advertising intervals — the short timeout was a Windows-specific
# need and shouldn't narrow macOS/Linux discovery.
SCAN_TIMEOUT_SECS = 3.0 if sys.platform == "win32" else 10.0

# Exponential backoff for reconnection: if the device is resetting or rejecting
# us, we don't want to hammer it. After each failure we double the wait up to
# RECONNECT_BACKOFF_MAX; a successful connection that survives at least
# STABLE_CONNECTION_SECS resets the backoff.
RECONNECT_BACKOFF_BASE_SECS = 3.0
RECONNECT_BACKOFF_MAX_SECS = 60.0
STABLE_CONNECTION_SECS = 30.0

# Windows: BluetoothLEAdvertisementWatcher can silently stop delivering
# callbacks while reporting status=Started. After this many consecutive
# scan-timeout misses with the radio ON, we programmatically toggle the
# radio off→on to recover without user intervention.
#
# Kept deliberately conservative: power-cycling the radio briefly drops ALL
# of the user's Bluetooth devices (mouse, keyboard, headphones), so we only
# resort to it after a sustained run of misses (~25s at a 3s scan timeout +
# base backoff) rather than reacting to a transient scan glitch.
RADIO_RESET_AFTER_MISSES = 5

# A single power-cycle doesn't always revive the watcher. Re-arm the reset
# after each fresh run of RADIO_RESET_AFTER_MISSES misses, but cap the total
# attempts per disconnected spell and enforce a cooldown between them — so a
# genuinely dead watcher is retried (instead of giving up after one shot and
# stranding the user back at manual BT-toggling), without power-cycling the
# user's whole radio on a tight loop. The counter and cap reset on a successful
# connect. After the cap we stop trying and fall back to plain backoff.
RADIO_RESET_MAX_ATTEMPTS = 3
RADIO_RESET_COOLDOWN_SECS = 120.0

# Handler for lines received from the stick (device → daemon).
IncomingHandler = Callable[[dict[str, Any]], Awaitable[None]]


class BuddyBLE:
    def __init__(
        self,
        on_message: IncomingHandler,
        name_prefix: str = DEFAULT_NAME_PREFIX,
        address: Optional[str] = None,
    ) -> None:
        self.on_message = on_message
        self.name_prefix = name_prefix
        self.address = address  # if provided, skip scanning
        self._client: Optional[BleakClient] = None
        self._assembler = LineAssembler()
        self._connected_evt = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._stop = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def wait_connected(self) -> None:
        await self._connected_evt.wait()

    async def send(self, obj: dict[str, Any], codec: Optional[str] = None) -> bool:
        """Write a newline-terminated JSON object to the stick's RX. Returns True on success.

        ``codec`` is passed through to ``protocol.encode``. Default (``None``)
        means UTF-8 JSON; ``'gbk'`` / ``'big5'`` / ``'shift_jis'`` switch the
        wire to the matching CJK firmware variant's expected byte encoding
        (see protocol.CJK_CODECS).
        """
        if not self.connected or self._client is None:
            return False
        data = encode(obj, codec=codec)
        try:
            async with self._send_lock:
                # ATT Write Without Response payload = MTU - 3 bytes overhead.
                # Chunk so multi-byte UTF-8 sequences never straddle a packet boundary.
                chunk_size = max(20, self._client.mtu_size - 3)
                for chunk in _utf8_safe_chunks(data, chunk_size):
                    await self._client.write_gatt_char(NUS_RX_UUID, chunk, response=False)
                    # Yield so the BLE host stack can drain Write Without Response
                    # credits before the next chunk; prevents silent drops.
                    await asyncio.sleep(0)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("ble send failed: %s", e)
            return False

    async def run(self) -> None:
        """Long-running connect/serve/reconnect loop. Exits when stop() is called.

        Uses exponential backoff so a misbehaving peripheral (e.g. firmware in
        a reset loop, bonding confusion) gets breathing room instead of being
        hammered every 3 seconds."""
        backoff = RECONNECT_BACKOFF_BASE_SECS
        consecutive_misses = 0
        # Radio-reset recovery state. Re-armed by the miss counter (not a
        # one-shot flag): another reset is allowed once misses reach a fresh
        # RADIO_RESET_AFTER_MISSES beyond the last reset, capped at
        # RADIO_RESET_MAX_ATTEMPTS and gated by RADIO_RESET_COOLDOWN_SECS, until
        # a successful connect clears it. This makes a dead watcher retryable
        # instead of giving up after one shot.
        reset_attempts = 0
        misses_at_last_reset = 0
        last_reset_ts = 0.0
        while not self._stop.is_set():
            connect_ts: float | None = None
            try:
                device = await self._find_device()
                if device is None:
                    consecutive_misses += 1
                    log.info("no buddy device found, retrying in %.1fs (miss #%d)",
                             backoff, consecutive_misses)
                    # Eligible for a radio reset when: we've hit a fresh run of
                    # RADIO_RESET_AFTER_MISSES since the last attempt, we're under
                    # the attempt cap, and the cooldown has elapsed.
                    now = time.monotonic()
                    eligible = (
                        consecutive_misses - misses_at_last_reset >= RADIO_RESET_AFTER_MISSES
                        and reset_attempts < RADIO_RESET_MAX_ATTEMPTS
                        and (last_reset_ts == 0.0 or now - last_reset_ts >= RADIO_RESET_COOLDOWN_SECS)
                    )
                    reset_fired = False
                    if eligible:
                        reset_fired = await self._try_reset_radio()
                        if reset_fired:
                            reset_attempts += 1
                            misses_at_last_reset = consecutive_misses
                            last_reset_ts = time.monotonic()
                            if reset_attempts >= RADIO_RESET_MAX_ATTEMPTS:
                                log.warning(
                                    "radio reset: %d attempts made without recovery — "
                                    "giving up auto-reset; may need a manual BT toggle",
                                    reset_attempts,
                                )
                    if reset_fired:
                        # Radio was power-cycled (already slept ~4s inside). Give the
                        # device a fresh fast attempt at the base backoff.
                        backoff = RECONNECT_BACKOFF_BASE_SECS
                        continue
                    # No reset this iteration (below threshold, capped, cooling
                    # down, or a no-op platform/path). Back off with a real sleep
                    # so we never busy-loop.
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_SECS)
                    continue
                consecutive_misses = 0
                # Successful find: clear all reset state so the next disconnected
                # spell starts fresh and is fully retryable again.
                reset_attempts = 0
                misses_at_last_reset = 0
                last_reset_ts = 0.0
                log.info("connecting to %s (%s)", device.name, device.address)
                async with BleakClient(device) as client:
                    self._client = client
                    self._assembler = LineAssembler()
                    await client.start_notify(NUS_TX_UUID, self._on_notify)
                    self._connected_evt.set()
                    connect_ts = time.monotonic()
                    log.info("connected, subscribed to TX notify")
                    # Hold the connection open until it drops or we're told to stop.
                    while client.is_connected and not self._stop.is_set():
                        await asyncio.sleep(1.0)
                    # Clear the connected event the instant we observe the drop —
                    # NOT in the finally below. BleakClient.__aexit__ teardown can
                    # take a while, and during that window `connected` is already
                    # False while the event is still set. A consumer that loops on
                    # wait_connected() (daemon._on_ble_connected) would then wake on
                    # the stale event, find connected False, and busy-loop with no
                    # await until teardown finally clears it. Clear it here to close
                    # that race.
                    self._connected_evt.clear()
                    lifetime = time.monotonic() - connect_ts
                    log.info("disconnected after %.1fs", lifetime)
                    # Clear reset state so the post-disconnect reconnect spell is
                    # fully retryable from scratch (fresh attempt budget + no
                    # stale cooldown).
                    reset_attempts = 0
                    misses_at_last_reset = 0
                    last_reset_ts = 0.0
            except Exception as e:  # noqa: BLE001
                log.warning("ble connection error: %s", e)
            finally:
                self._client = None
                self._connected_evt.clear()
            if not self._stop.is_set():
                # Reset backoff if the last connection was stable for a while —
                # a brief single disconnect shouldn't inherit flapping penalty.
                if connect_ts is not None and (time.monotonic() - connect_ts) >= STABLE_CONNECTION_SECS:
                    backoff = RECONNECT_BACKOFF_BASE_SECS
                log.info("waiting %.1fs before reconnect", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_SECS)

    async def stop(self) -> None:
        self._stop.set()
        if self._client is not None and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # ---- internals ----

    async def _try_reset_radio(self) -> bool:
        """Windows only: toggle the BT radio off→on to unstick the WinRT
        advertisement watcher. The watcher can silently stop delivering
        callbacks while reporting status=Started; a radio power-cycle clears
        it without user intervention.

        Returns ``True`` only if the radio was actually power-cycled, so the
        caller can reset its miss-counter/backoff only when something was done.
        Returns ``False`` on non-Windows platforms and on any path that did not
        toggle the radio (no adapter, radio already off, denied, error) — the
        caller then falls through to its normal backoff-with-sleep instead of
        spinning. This keeps the Windows-specific recovery from bleeding into
        the macOS/Linux backoff cadence (where it is always a no-op)."""
        if sys.platform != "win32":
            return False
        try:
            from winrt.windows.devices.bluetooth import BluetoothAdapter
            from winrt.windows.devices.radios import RadioAccessStatus, RadioState
            adapter = await BluetoothAdapter.get_default_async()
            if adapter is None:
                log.warning("radio reset: no BT adapter found, skipping")
                return False
            radio = await adapter.get_radio_async()
            if radio.state != RadioState.ON:
                # Radio is off (user likely turned BT off deliberately). Don't
                # block waiting for it — return immediately. We return False, so
                # the caller does NOT update last_reset_ts and the cooldown does
                # not engage on this path; what prevents a tight loop is the
                # caller's `await asyncio.sleep(backoff)` on a no-reset miss. The
                # cost while BT stays off is one cheap WinRT state query per
                # backoff cycle, which is fine. (Previously this branch spun a
                # 20s wait every eligible cycle.)
                log.info("radio reset: radio is %s, not powering — leaving it to the user", radio.state)
                return False
            log.warning(
                "radio reset: %d consecutive scan misses — power-cycling the BT "
                "radio to recover. This briefly disconnects ALL Bluetooth devices "
                "(mouse, keyboard, headphones), not just the buddy.",
                RADIO_RESET_AFTER_MISSES,
            )
            status = await radio.set_state_async(RadioState.OFF)
            if status != RadioAccessStatus.ALLOWED:
                log.warning("radio reset: could not turn radio off (status=%s) — toggle BT manually", status)
                return False
            await asyncio.sleep(2.0)
            status = await radio.set_state_async(RadioState.ON)
            if status != RadioAccessStatus.ALLOWED:
                log.warning("radio reset: could not turn radio back on (status=%s)", status)
                return False
            await asyncio.sleep(2.0)
            log.info("radio reset: BT radio cycled successfully")
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("radio reset failed: %s", e)
            return False

    async def _find_device(self) -> Optional[BLEDevice]:
        if self.address is not None:
            return await BleakScanner.find_device_by_address(self.address, timeout=SCAN_TIMEOUT_SECS)

        def _match(d: BLEDevice, adv) -> bool:  # type: ignore[no-untyped-def]
            name = (adv.local_name or d.name) or ""
            return name.startswith(self.name_prefix)

        return await BleakScanner.find_device_by_filter(_match, timeout=SCAN_TIMEOUT_SECS)

    def _on_notify(self, _handle: Any, data: bytearray) -> None:
        for obj in self._assembler.feed(bytes(data)):
            # Hand off to the daemon's asyncio loop. We're already in it (bleak
            # on macOS dispatches via asyncio), so scheduling is safe.
            asyncio.create_task(self._dispatch(obj))

    async def _dispatch(self, obj: dict[str, Any]) -> None:
        try:
            await self.on_message(obj)
        except Exception:  # noqa: BLE001
            log.exception("on_message handler crashed")


def _utf8_safe_chunks(data: bytes, max_size: int) -> list[bytes]:
    """Split UTF-8 bytes without ending a chunk inside a codepoint.

    The firmware consumes each BLE write independently, so a raw byte slice that
    ends between the bytes of a Chinese character can render as mojibake. The
    input comes from protocol.encode(), so it is valid UTF-8; we only need to
    back up from continuation bytes at the proposed boundary.
    """
    if max_size <= 0:
        raise ValueError("max_size must be positive")

    chunks: list[bytes] = []
    offset = 0
    while offset < len(data):
        end = min(offset + max_size, len(data))
        if end < len(data):
            safe_end = end
            while safe_end > offset and _is_utf8_continuation(data[safe_end]):
                safe_end -= 1
            if safe_end > offset:
                end = safe_end
            else:
                end = min(offset + _utf8_codepoint_size(data[offset]), len(data))
        chunks.append(data[offset:end])
        offset = end
    return chunks


def _is_utf8_continuation(byte: int) -> bool:
    return (byte & 0b1100_0000) == 0b1000_0000


def _utf8_codepoint_size(lead_byte: int) -> int:
    if (lead_byte & 0b1000_0000) == 0:
        return 1
    if (lead_byte & 0b1110_0000) == 0b1100_0000:
        return 2
    if (lead_byte & 0b1111_0000) == 0b1110_0000:
        return 3
    if (lead_byte & 0b1111_1000) == 0b1111_0000:
        return 4
    return 1
