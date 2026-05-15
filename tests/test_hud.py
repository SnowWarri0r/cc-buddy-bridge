"""Unit tests for hud.format_line — pure formatting, no IPC."""

from __future__ import annotations

from cc_buddy_bridge.hud import BAR_EMPTY, BAR_FULL, BAR_WIDTH, _bar, _format_tokens, format_line


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
    assert BAR_FULL in out  # the battery bar rendered
    assert "🔒" in out
    assert "2run" in out
    ascii_out = format_line(state, ascii_only=True)
    assert "96%" in ascii_out
    assert "[" in ascii_out and "]" in ascii_out
    assert "lock" in ascii_out
    assert "2run" in ascii_out


def test_format_low_battery_icon():
    state = {"ble_connected": True, "sec": True, "battery_pct": 10}
    assert "🪫" in format_line(state)
    # Low battery should be coloured red via ANSI.
    assert "\033[31m" in format_line(state)


def test_bar_rendering_endpoints():
    assert _bar(0) == BAR_EMPTY * BAR_WIDTH
    assert _bar(100) == BAR_FULL * BAR_WIDTH


def test_bar_rendering_middle():
    # 50% should be roughly half full.
    bar = _bar(50)
    assert bar.count(BAR_FULL) == BAR_WIDTH // 2
    assert bar.count(BAR_EMPTY) == BAR_WIDTH // 2


def test_bar_clamps_out_of_range():
    assert _bar(-10) == BAR_EMPTY * BAR_WIDTH
    assert _bar(200) == BAR_FULL * BAR_WIDTH


def test_ascii_bar_uses_ascii_chars_only():
    state = {"ble_connected": True, "sec": True, "battery_pct": 60}
    ascii_out = format_line(state, ascii_only=True)
    assert BAR_FULL not in ascii_out
    assert BAR_EMPTY not in ascii_out
    assert "=" in ascii_out or "-" in ascii_out


def test_format_unencrypted_warns():
    state = {"ble_connected": True, "sec": False, "battery_pct": 80}
    assert "UNSEC" in format_line(state)


def test_format_no_running_omits_count():
    state = {"ble_connected": True, "sec": True, "battery_pct": 80, "running": 0}
    out = format_line(state)
    assert "run" not in out


def test_format_cost_shows_when_above_one_cent():
    state = {"ble_connected": True, "sec": True, "battery_pct": 80, "cost_today": 0.42}
    out = format_line(state)
    assert "$0.42" in out
    ascii_out = format_line(state, ascii_only=True)
    assert "$0.42" in ascii_out


def test_format_cost_hidden_below_one_cent():
    state = {"ble_connected": True, "sec": True, "battery_pct": 80, "cost_today": 0.004}
    out = format_line(state)
    assert "$" not in out


def test_format_cost_two_decimals_for_double_digits():
    state = {"ble_connected": True, "sec": True, "battery_pct": 80, "cost_today": 12.5}
    out = format_line(state)
    assert "$12.50" in out


def test_token_formatter_thresholds():
    assert _format_tokens(0) == "0"
    assert _format_tokens(999) == "999"
    assert _format_tokens(1_000) == "1.0K"
    assert _format_tokens(12_345) == "12.3K"
    assert _format_tokens(99_999) == "100.0K"  # right at the 100K boundary
    assert _format_tokens(100_000) == "100K"   # past it, drop the decimal
    assert _format_tokens(850_000) == "850K"
    assert _format_tokens(999_999) == "999K"
    assert _format_tokens(1_000_000) == "1.0M"
    assert _format_tokens(1_234_567) == "1.2M"


def test_format_tokens_shown_when_above_1k():
    state = {"ble_connected": True, "sec": True, "battery_pct": 80, "tokens_today": 12_345}
    out = format_line(state)
    assert "12.3K" in out


def test_format_tokens_hidden_below_1k():
    state = {"ble_connected": True, "sec": True, "battery_pct": 80, "tokens_today": 850}
    out = format_line(state)
    # Don't render single-figure token counts — the line is for at-a-glance.
    assert "850" not in out


def test_format_tokens_and_cost_appear_together():
    state = {
        "ble_connected": True, "sec": True, "battery_pct": 80,
        "tokens_today": 1_234_567, "cost_today": 4.20,
    }
    out = format_line(state)
    # Tokens render before cost (input → derived spend reads naturally L→R).
    assert out.index("1.2M") < out.index("$4.20")
