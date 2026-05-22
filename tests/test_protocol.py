
from cc_buddy_bridge.ble import _utf8_safe_chunks
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


def test_heartbeat_omits_completed_when_idle():
    s = State()
    hb = build_heartbeat(s)
    assert "completed" not in hb


def test_heartbeat_includes_completed_during_pulse():
    s = State()
    s.pulse_completed(duration_secs=2.0)
    hb = build_heartbeat(s)
    assert hb.get("completed") is True


def test_heartbeat_drops_completed_after_pulse_expires():
    s = State()
    s.pulse_completed(duration_secs=-1.0)  # already expired
    hb = build_heartbeat(s)
    assert "completed" not in hb


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


def test_utf8_safe_chunks_do_not_split_cjk_at_boundary():
    data = encode({"msg": "ab你好cd"})
    first_chinese_byte = data.index("你".encode("utf-8"))
    max_size = first_chinese_byte + 1

    chunks = _utf8_safe_chunks(data, max_size)

    assert b"".join(chunks) == data
    assert all(chunk.decode("utf-8") for chunk in chunks)
    assert all(len(chunk) <= max_size for chunk in chunks)


def test_utf8_safe_chunks_keeps_codepoint_when_max_size_is_tiny():
    data = "你".encode("utf-8")

    chunks = _utf8_safe_chunks(data, 1)

    assert chunks == [data]


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

def test_sanitize_keeps_ascii():
    assert sanitize_for_stick("hello world 123 !@#") == "hello world 123 !@#"


def test_sanitize_strips_cjk():
    # The stock firmware does not enable HZK16. Letting CJK through crashes
    # the BLE link on the stick — see protocol.sanitize_for_stick docstring.
    out = sanitize_for_stick("hello 你好 world")
    assert "你" not in out and "好" not in out
    assert "hello" in out and "world" in out


def test_sanitize_strips_bmp_symbols():
    # Same firmware-glyph-table issue as CJK.
    out = sanitize_for_stick("› done ✓")
    assert "›" not in out
    assert "✓" not in out
    assert "done" in out


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
    assert "\x7f" not in sanitize_for_stick("hello\x7fworld")
    assert "\x85" not in sanitize_for_stick("hello\x85world")


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
