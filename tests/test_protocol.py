import json

from cc_buddy_bridge.protocol import (
    LineAssembler,
    build_heartbeat,
    build_turn_event,
    encode,
    sanitize_for_stick,
)
from cc_buddy_bridge.state import State


def test_heartbeat_empty_state():
    hb = build_heartbeat(State())
    assert hb["total"] == 0
    assert hb["running"] == 0
    assert hb["waiting"] == 0
    assert hb["entries"] == []
    assert hb["tokens"] == 0
    assert "prompt" not in hb


def test_heartbeat_with_pending():
    s = State()
    s.session_start("x")
    s.permission_pending("x", "tid_1", "Bash", "rm -rf /tmp/foo")
    s.turn_begin("x")
    hb = build_heartbeat(s)
    assert hb["total"] == 1
    assert hb["running"] == 1
    assert hb["waiting"] == 1
    assert hb["msg"] == "approve: Bash"
    assert hb["prompt"]["id"] == "tid_1"
    assert hb["prompt"]["tool"] == "Bash"
    assert hb["prompt"]["hint"].startswith("rm -rf")


def test_heartbeat_entries_formatted():
    s = State()
    s.add_entry("hello world", at=0)  # epoch 0 → local HH:MM
    hb = build_heartbeat(s)
    assert len(hb["entries"]) == 1
    # Should be "HH:MM hello world" — just check suffix since HH:MM is tz-local.
    assert hb["entries"][0].endswith(" hello world")


def test_heartbeat_entries_on_wire_are_oldest_first():
    """State keeps newest-first for cheap prepend; the wire format is reversed
    so the firmware's ``lines[n-1]=newest`` assumption holds."""
    s = State()
    s.add_entry("oldest", at=0)
    s.add_entry("middle", at=0)
    s.add_entry("newest", at=0)
    hb = build_heartbeat(s)
    # On wire: oldest → middle → newest
    assert hb["entries"][0].endswith(" oldest")
    assert hb["entries"][1].endswith(" middle")
    assert hb["entries"][2].endswith(" newest")


def test_turn_event_size_cap():
    huge = [{"type": "text", "text": "x" * 5000}]
    assert build_turn_event("assistant", huge) is None


def test_turn_event_ok():
    evt = build_turn_event("assistant", [{"type": "text", "text": "hi"}])
    assert evt is not None
    assert evt["evt"] == "turn"
    assert evt["role"] == "assistant"


def test_encode_terminates_with_newline():
    buf = encode({"a": 1})
    assert buf.endswith(b"\n")


def test_line_assembler_fragments():
    la = LineAssembler()
    out = la.feed(b'{"a":1}\n{"b":')
    assert out == [{"a": 1}]
    out = la.feed(b"2}\n")
    assert out == [{"b": 2}]


def test_line_assembler_drops_bad_lines():
    la = LineAssembler()
    out = la.feed(b'garbage\n{"ok":true}\n')
    assert out == [{"ok": True}]


def test_line_assembler_empty_lines_ignored():
    la = LineAssembler()
    out = la.feed(b"\n\n\n")
    assert out == []


# ---- sanitize_for_stick ----

def test_sanitize_keeps_ascii_and_cjk():
    assert sanitize_for_stick("hello 你好 world") == "hello 你好 world"


def test_sanitize_keeps_bmp_symbols():
    # › U+203A and ✓ U+2713 are in the BMP; we actively use them in entries.
    assert sanitize_for_stick("› done ✓") == "› done ✓"


def test_sanitize_strips_emoji():
    # 🎮 is U+1F3AE (supplementary plane).
    out = sanitize_for_stick("press A 🎮 now")
    assert "🎮" not in out
    assert "press A" in out and "now" in out


def test_sanitize_strips_multiple_emojis():
    out = sanitize_for_stick("🐾🎮🔴hello🌙")
    assert not any(ord(c) >= 0x10000 for c in out)
    assert "hello" in out


def test_sanitize_strips_newlines_and_control_chars():
    assert "\n" not in sanitize_for_stick("hello\nworld")
    assert "\x00" not in sanitize_for_stick("hello\x00world")


def test_sanitize_preserves_tab():
    assert sanitize_for_stick("a\tb") == "a\tb"


def test_sanitize_empty_string():
    assert sanitize_for_stick("") == ""


def test_heartbeat_sanitizes_prompt_hint():
    s = State()
    s.session_start("x")
    s.permission_pending("x", "tid_1", "Bash", "echo '🎮 emoji here'")
    hb = build_heartbeat(s)
    assert "🎮" not in hb["prompt"]["hint"]


def test_heartbeat_sanitizes_entries():
    s = State()
    s.add_entry("got 🐾 paw")
    hb = build_heartbeat(s)
    assert "🐾" not in hb["entries"][0]


def test_turn_event_sanitizes_nested_content():
    evt = build_turn_event("assistant", [{"type": "text", "text": "done 🎉"}])
    assert evt is not None
    assert "🎉" not in evt["content"][0]["text"]
