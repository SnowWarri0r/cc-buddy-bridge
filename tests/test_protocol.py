import json

from cc_buddy_bridge.protocol import (
    LineAssembler,
    build_heartbeat,
    build_turn_event,
    encode,
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
