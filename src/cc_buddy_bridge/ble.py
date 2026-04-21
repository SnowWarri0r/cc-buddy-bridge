"""BLE client that pairs with a claude-desktop-buddy device over Nordic UART Service.

Scans for peripherals whose advertised local name starts with "Claude", connects,
subscribes to TX notifications, and exposes an async `send()` method that writes
newline-terminated JSON to RX.

Uses bleak. macOS passes a CoreBluetooth-assigned UUID instead of a MAC address,
so the scan result is cached under the device's advertised name.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from .protocol import (
    LineAssembler,
    NUS_RX_UUID,
    NUS_TX_UUID,
    encode,
)

log = logging.getLogger(__name__)

# Default scan parameters.
DEFAULT_NAME_PREFIX = "Claude"
SCAN_TIMEOUT_SECS = 10.0

# Exponential backoff for reconnection: if the device is resetting or rejecting
# us, we don't want to hammer it. After each failure we double the wait up to
# RECONNECT_BACKOFF_MAX; a successful connection that survives at least
# STABLE_CONNECTION_SECS resets the backoff.
RECONNECT_BACKOFF_BASE_SECS = 3.0
RECONNECT_BACKOFF_MAX_SECS = 60.0
STABLE_CONNECTION_SECS = 30.0

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

    async def send(self, obj: dict[str, Any]) -> bool:
        """Write a newline-terminated JSON object to the stick's RX. Returns True on success."""
        if not self.connected or self._client is None:
            return False
        data = encode(obj)
        try:
            async with self._send_lock:
                # Write without response for throughput. MTU-sized chunks if needed.
                await self._client.write_gatt_char(NUS_RX_UUID, data, response=False)
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
        while not self._stop.is_set():
            connect_ts: float | None = None
            try:
                device = await self._find_device()
                if device is None:
                    log.info("no buddy device found, retrying in %.1fs", backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_SECS)
                    continue
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
