"""Tests for the Windows BT-radio-reset state machine in BuddyBLE.run().

The watcher-stuck recovery (RADIO_RESET_AFTER_MISSES, radio_reset_done) is the
most regression-prone part of the reconnect loop: it has a once-per-cycle guard
that must arm only on a real reset, must re-arm after a disconnect, and must
never busy-loop (every miss path that does not power-cycle the radio has to
sleep). These tests drive run() with scripted fakes so the loop terminates
deterministically instead of running forever.

No real BLE or WinRT is touched. Matches the repo convention of plain
asyncio.run() via a _run() helper rather than pytest-asyncio markers.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from cc_buddy_bridge import ble as ble_mod
from cc_buddy_bridge.ble import (
    RADIO_RESET_AFTER_MISSES,
    RADIO_RESET_MAX_ATTEMPTS,
    RECONNECT_BACKOFF_BASE_SECS,
    BuddyBLE,
)


def _run(coro):
    return asyncio.run(coro)


class _Harness:
    """Drives BuddyBLE.run() with scripted device finds and radio resets.

    - find_results: list of values _find_device returns in order. None = miss,
      a truthy sentinel = a found device (which we then immediately disconnect
      from, since BleakClient is faked out). After the list is exhausted we
      keep returning None.
    - reset_returns: list of bools _try_reset_radio returns in order.
    - Records every sleep duration and every reset call so assertions can read
      the resulting cadence. Stops the loop after `stop_after_misses` total
      misses so it never runs forever.
    """

    def __init__(self, find_results, reset_returns, stop_after_iterations=50):
        self._find_results = list(find_results)
        self._reset_returns = list(reset_returns)
        self.reset_calls = 0
        self.sleeps: list[float] = []
        self.miss_count = 0
        self.found_count = 0
        self._iterations = 0
        self._stop_after = stop_after_iterations
        self.clock = 0.0  # fake monotonic clock, advanced by _drive

    def install(self, ble: BuddyBLE) -> None:
        ble._find_device = self._find_device  # type: ignore[method-assign]
        ble._try_reset_radio = self._try_reset_radio  # type: ignore[method-assign]
        self._ble = ble

    async def _find_device(self):
        self._iterations += 1
        result: Optional[object] = self._find_results.pop(0) if self._find_results else None
        if result is None:
            self.miss_count += 1
        else:
            self.found_count += 1
        # Stop AFTER this iteration's result is processed: once we've produced
        # `stop_after` results, arm _stop so the loop exits at its next top-of-
        # loop check without processing a further phantom miss.
        if self._iterations >= self._stop_after:
            self._ble._stop.set()
        return result

    async def _try_reset_radio(self) -> bool:
        self.reset_calls += 1
        return self._reset_returns.pop(0) if self._reset_returns else False


def _make_ble() -> BuddyBLE:
    async def _noop(_obj):
        return None
    return BuddyBLE(on_message=_noop)


class _FakeDevice:
    name = "Claude-TEST"
    address = "AA:BB:CC:DD:EE:FF"


class _FakeClient:
    """Minimal async-context-manager stand-in for BleakClient.

    Reports connected once, lets run()'s hold-open loop see is_connected flip
    to False on the next check so the connection 'drops' immediately and the
    loop proceeds to the post-disconnect bookkeeping.
    """

    def __init__(self, device, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        self._checks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):  # noqa: ANN002
        return False

    @property
    def is_connected(self) -> bool:
        # True on the first check (enter the hold-open loop), then False so it
        # exits after one iteration without a real wait.
        self._checks += 1
        return self._checks <= 1

    async def start_notify(self, _uuid, _cb):  # noqa: ANN001
        return None


# ---- miss-path state machine ---------------------------------------------


def test_reset_fires_at_threshold_not_before():
    """The radio reset is attempted only once misses reach the threshold."""
    ble = _make_ble()
    # All misses, reset always succeeds when called.
    h = _Harness(find_results=[], reset_returns=[True], stop_after_iterations=RADIO_RESET_AFTER_MISSES)
    h.install(ble)

    _run(_drive(ble, h))

    # Reset must not fire before RADIO_RESET_AFTER_MISSES misses accrued.
    assert h.reset_calls == 1, f"expected exactly one reset at threshold, got {h.reset_calls}"


def test_no_reset_below_threshold():
    """Fewer misses than the threshold => no radio reset at all."""
    ble = _make_ble()
    h = _Harness(find_results=[], reset_returns=[], stop_after_iterations=RADIO_RESET_AFTER_MISSES - 1)
    h.install(ble)

    _run(_drive(ble, h))

    assert h.reset_calls == 0
    # Every miss below threshold must have slept (no busy-loop).
    assert len(h.sleeps) == RADIO_RESET_AFTER_MISSES - 1
    assert all(s > 0 for s in h.sleeps)


def test_failed_reset_does_not_arm_guard_and_still_sleeps():
    """A no-op/failed reset (returns False) must NOT suppress later attempts
    and must fall through to a real sleep (no busy-loop)."""
    ble = _make_ble()
    # Reset returns False the first time it's eligible, then True next cycle.
    h = _Harness(find_results=[], reset_returns=[False, True], stop_after_iterations=RADIO_RESET_AFTER_MISSES + 2)
    h.install(ble)

    _run(_drive(ble, h))

    # Reset attempted at least twice: once failing, then re-attempted because
    # the guard was never armed by the False return.
    assert h.reset_calls >= 2, f"failed reset should be retried, got {h.reset_calls} calls"
    # The miss that saw a False reset must still have produced a sleep.
    assert len(h.sleeps) >= 1
    assert all(s > 0 for s in h.sleeps)


def test_successful_reset_resets_backoff_to_base():
    """A successful reset resets the miss-counter and backoff to base, so the
    next miss logs the base backoff rather than an escalated one."""
    ble = _make_ble()
    h = _Harness(find_results=[], reset_returns=[True], stop_after_iterations=RADIO_RESET_AFTER_MISSES + 1)
    h.install(ble)

    _run(_drive(ble, h))

    assert h.reset_calls == 1
    # After the successful reset (which itself does not sleep via our patched
    # path), the following miss sleeps at base backoff again.
    assert h.sleeps, "expected at least one sleep after reset cycle"
    assert h.sleeps[-1] == RECONNECT_BACKOFF_BASE_SECS


def test_reset_retries_when_first_reset_does_not_help():
    """BLOCKING-bug regression (review #1): if a successful power-cycle does NOT
    revive the watcher and no connection happens, the reset must be RETRIED on a
    later run of misses — not give up after one shot and strand the user.

    Old behaviour: radio_reset_done latched True after the first reset and only
    cleared on connect/disconnect, so a never-connecting daemon reset exactly
    once then sat in max backoff forever (manual BT toggle needed — the thing
    this feature removes).
    """
    ble = _make_ble()
    # Never finds the device; reset always 'succeeds' (radio toggles) but it
    # never actually revives discovery. Run long enough for several re-arm
    # windows + cooldowns to elapse.
    h = _Harness(find_results=[], reset_returns=[True] * 10, stop_after_iterations=80)
    h.install(ble)

    _run(_drive(ble, h))

    # Must have retried beyond the first attempt, up to the cap.
    assert 2 <= h.reset_calls <= RADIO_RESET_MAX_ATTEMPTS, (
        f"reset must retry when it doesn't help (>=2) and respect the cap "
        f"(<={RADIO_RESET_MAX_ATTEMPTS}); got {h.reset_calls}"
    )


def test_reset_attempts_capped():
    """Reset must stop after RADIO_RESET_MAX_ATTEMPTS — never power-cycle the
    user's whole radio in an unbounded loop."""
    ble = _make_ble()
    h = _Harness(find_results=[], reset_returns=[True] * 50, stop_after_iterations=200)
    h.install(ble)

    _run(_drive(ble, h))

    assert h.reset_calls <= RADIO_RESET_MAX_ATTEMPTS, (
        f"reset attempts must be capped at {RADIO_RESET_MAX_ATTEMPTS}, "
        f"got {h.reset_calls}"
    )


async def _drive(ble: BuddyBLE, h: _Harness):
    """Run the loop with asyncio.sleep instant+recorded, and a fake monotonic
    clock that advances by each sleep's duration.

    Advancing the clock matters for the radio-reset cooldown: the reset re-arm
    is gated on RADIO_RESET_COOLDOWN_SECS of monotonic time, so without
    advancing the clock the cooldown would never elapse and re-arm would never
    fire in tests. Each recorded sleep pushes the fake clock forward, plus a
    small fixed step per loop so even zero-sleep iterations make progress."""
    real_sleep = asyncio.sleep
    h.clock = 1000.0  # arbitrary non-zero start

    async def _fast_sleep(secs):
        h.sleeps.append(secs)
        h.clock += max(secs, 0.0)
        await real_sleep(0)

    def _fake_monotonic():
        h.clock += 0.001  # tiny per-call advance so ordering is monotonic
        return h.clock

    orig_sleep = ble_mod.asyncio.sleep
    orig_mono = ble_mod.time.monotonic
    ble_mod.asyncio.sleep = _fast_sleep  # type: ignore[assignment]
    ble_mod.time.monotonic = _fake_monotonic  # type: ignore[assignment]
    try:
        await ble.run()
    finally:
        ble_mod.asyncio.sleep = orig_sleep  # type: ignore[assignment]
        ble_mod.time.monotonic = orig_mono  # type: ignore[assignment]


# ---- disconnect-reset path -----------------------------------------------


def test_reset_rearmed_after_disconnect(monkeypatch):
    """A connection that drops must clear the once-per-cycle guard so a fresh
    run of misses can power-cycle the radio again.

    This is the regression that commit 4 had to fix: radio_reset_done was
    staying True across a disconnect, so after the watch slept and dropped its
    link, the daemon could never reset the radio again and got stuck in a long
    backoff without recovering.
    """
    monkeypatch.setattr(ble_mod, "BleakClient", _FakeClient)
    ble = _make_ble()

    # Sequence: enough misses to fire reset #1, then a successful find (which
    # connects+drops via _FakeClient), then another run of misses that must be
    # able to fire reset #2.
    finds = (
        [None] * RADIO_RESET_AFTER_MISSES          # accrue to threshold -> reset #1
        + [_FakeDevice()]                          # connect + immediate drop
        + [None] * RADIO_RESET_AFTER_MISSES         # accrue again -> reset #2 (only if re-armed)
    )
    total_iters = len(finds)
    h = _Harness(find_results=finds, reset_returns=[True, True], stop_after_iterations=total_iters)
    h.install(ble)

    _run(_drive(ble, h))

    assert h.found_count == 1, "fake device should have been 'found' once"
    assert h.reset_calls == 2, (
        f"reset must re-arm after disconnect and fire twice, got {h.reset_calls} "
        "(if 1, radio_reset_done stayed True across the disconnect — the commit-4 bug)"
    )


def test_connected_event_cleared_after_disconnect(monkeypatch):
    """After a connect/drop cycle, the connected-event must be CLEAR.

    Regression guard for the daemon CPU-spin: daemon._on_ble_connected loops on
    `await ble.wait_connected()`. If run() leaves _connected_evt SET after the
    link drops (clearing it only in a late finally, after slow BleakClient
    teardown), the consumer wakes on the stale event, finds connected False, and
    busy-loops with no await — pinning a core until teardown finally clears it.
    run() must clear the event the instant it observes the drop.
    """
    monkeypatch.setattr(ble_mod, "BleakClient", _FakeClient)
    ble = _make_ble()
    # One find -> connect via _FakeClient (drops immediately), then stop.
    finds = [_FakeDevice()]
    h = _Harness(find_results=finds, reset_returns=[], stop_after_iterations=1)
    h.install(ble)

    _run(_drive(ble, h))

    assert h.found_count == 1, "fake device should have connected once"
    assert not ble._connected_evt.is_set(), (
        "connected-event must be CLEAR after the link drops — if set, "
        "daemon._on_ble_connected would busy-loop on the stale event (the spin)"
    )
