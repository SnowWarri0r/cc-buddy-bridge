"""Unit tests for hud.format_line — pure formatting, no IPC."""

from __future__ import annotations

from cc_buddy_bridge.hud import format_line


def test_format_none_state():
    assert format_line(None) == "🐾 off"
    assert format_line(None, ascii_only=True) == "buddy: off"


def test_format_disconnected():
    state = {"ble_connected": False}
    assert format_line(state) == "🐾 ∅"
    assert format_line(state, ascii_only=True) == "buddy: disc"


def test_format_pending_permission_takes_over():
    state = {
        "ble_connected": True,
        "pending_tool": "Bash",
        "battery_pct": 80,  # ignored because pending dominates
    }
    out = format_line(state)
    assert "approve" in out and "Bash" in out
    assert "80" not in out
    assert format_line(state, ascii_only=True) == "buddy: ASK Bash"


def test_format_full_state():
    state = {
        "ble_connected": True,
        "sec": True,
        "battery_pct": 96,
        "running": 2,
        "total": 2,
    }
    out = format_line(state)
    assert "96%" in out
    assert "🔒" in out
    assert "2run" in out
    ascii_out = format_line(state, ascii_only=True)
    assert "96%" in ascii_out
    assert "lock" in ascii_out
    assert "2run" in ascii_out


def test_format_low_battery_icon():
    state = {"ble_connected": True, "sec": True, "battery_pct": 10}
    assert "🪫" in format_line(state)
    assert "🔒" not in format_line(state) or "🪫" in format_line(state)  # both ok


def test_format_unencrypted_warns():
    state = {"ble_connected": True, "sec": False, "battery_pct": 80}
    assert "UNSEC" in format_line(state)


def test_format_no_running_omits_count():
    state = {"ble_connected": True, "sec": True, "battery_pct": 80, "running": 0}
    out = format_line(state)
    assert "run" not in out
