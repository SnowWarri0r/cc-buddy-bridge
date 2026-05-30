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
SCAN_TIMEOUT_SECS = 3.0

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
RADIO_RESET_AFTER_MISSES = 2

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
        radio_reset_done = False  # reset once per connection attempt cycle
        while not self._stop.is_set():
            connect_ts: float | None = None
            try:
                device = await self._find_device()
                if device is None:
                    consecutive_misses += 1
                    log.info("no buddy device found, retrying in %.1fs (miss #%d)",
                             backoff, consecutive_misses)
                    if consecutive_misses >= RADIO_RESET_AFTER_MISSES and not radio_reset_done:
                        await self._try_reset_radio()
                        radio_reset_done = True
                        consecutive_misses = 0
                        backoff = RECONNECT_BACKOFF_BASE_SECS
                    else:
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_SECS)
                    continue
                consecutive_misses = 0
                radio_reset_done = False  # clear so next disconnection gets a reset attempt
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
                    lifetime = time.monotonic() - connect_ts
                    log.info("disconnected after %.1fs", lifetime)
                    radio_reset_done = False  # allow reset on next scan cycle after disconnect
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

    async def _try_reset_radio(self) -> None:
        """Windows only: toggle the BT radio off→on to unstick the WinRT
        advertisement watcher. The watcher can silently stop delivering
        callbacks while reporting status=Started; a radio power-cycle clears
        it without user intervention. No-op on non-Windows platforms."""
        if sys.platform != "win32":
            return
        try:
            from winrt.windows.devices.bluetooth import BluetoothAdapter
            from winrt.windows.devices.radios import RadioAccessStatus, RadioState
            adapter = await BluetoothAdapter.get_default_async()
            if adapter is None:
                log.warning("radio reset: no BT adapter found, skipping")
                return
            radio = await adapter.get_radio_async()
            if radio.state != RadioState.ON:
                log.info("radio reset: radio already off (state=%s), waiting for it to come back on", radio.state)
                for _ in range(20):
                    await asyncio.sleep(1.0)
                    radio = await adapter.get_radio_async()
                    if radio.state == RadioState.ON:
                        break
                return
            log.warning("radio reset: %d consecutive scan misses — toggling BT radio to recover",
                        RADIO_RESET_AFTER_MISSES)
            status = await radio.set_state_async(RadioState.OFF)
            if status != RadioAccessStatus.ALLOWED:
                log.warning("radio reset: could not turn radio off (status=%s) — toggle BT manually", status)
                return
            await asyncio.sleep(2.0)
            status = await radio.set_state_async(RadioState.ON)
            if status != RadioAccessStatus.ALLOWED:
                log.warning("radio reset: could not turn radio back on (status=%s)", status)
                return
            await asyncio.sleep(2.0)
            log.info("radio reset: BT radio cycled successfully")
        except Exception as e:  # noqa: BLE001
            log.warning("radio reset failed: %s", e)

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
