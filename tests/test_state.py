import time

from cc_buddy_bridge.state import State


def test_session_lifecycle():
    s = State()
    s.session_start("a", transcript_path="/tmp/a.jsonl", cwd="/tmp")
    s.session_start("b")
    assert s.total == 2
    s.session_end("a")
    assert s.total == 1
    assert "b" in s.sessions


def test_turn_running_count():
    s = State()
    s.session_start("x")
    s.session_start("y")
    s.turn_begin("x")
    assert s.running_count == 1
    s.turn_begin("y")
    assert s.running_count == 2
    s.turn_end("x")
    assert s.running_count == 1


def test_permission_pending_and_resolve():
    s = State()
    s.session_start("x")
    p = s.permission_pending("x", "tid_1", "Bash", "rm -rf /tmp/foo")
    assert s.waiting_count == 1
    assert s.first_pending() is p
    resolved = s.permission_resolved("tid_1")
    assert resolved is p
    assert s.waiting_count == 0
    assert s.first_pending() is None


def test_permission_pending_on_unknown_session_auto_creates():
    s = State()
    s.permission_pending("zzz", "tid_X", "Bash", "cmd")
    assert s.waiting_count == 1
    assert "zzz" in s.sessions


def test_entries_newest_first_and_capped():
    s = State()
    for i in range(20):
        s.add_entry(f"line {i}")
    assert len(s.entries) == State.MAX_ENTRIES
    # newest first
    assert s.entries[0].text == "line 19"


def test_tokens_setter():
    s = State()
    s.set_tokens(123, 45)
    assert s.tokens_cumulative == 123
    assert s.tokens_today == 45
    # Cost params default to 0 when omitted
    assert s.cost_cumulative == 0.0
    assert s.cost_today == 0.0


def test_tokens_setter_with_cost():
    s = State()
    s.set_tokens(1000, 200, cost_cumulative=12.34, cost_today=2.50)
    assert s.cost_cumulative == 12.34
    assert s.cost_today == 2.50


def test_first_pending_picks_oldest():
    s = State()
    s.session_start("a")
    s.session_start("b")
    p_a = s.permission_pending("a", "t1", "Bash", "cmd1")
    time.sleep(0.01)
    s.permission_pending("b", "t2", "Edit", "cmd2")
    assert s.first_pending() is p_a
