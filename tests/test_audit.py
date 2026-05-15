"""Tests for the audit log module."""

from __future__ import annotations

import json
from pathlib import Path

from cc_buddy_bridge.audit import AuditLog, default_path


def test_default_path_returns_path():
    p = default_path()
    assert isinstance(p, Path)
    # On all platforms the parent dir is somewhere under ~ (not absolute root).
    assert str(p).startswith(str(Path.home()))


def test_default_path_respects_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_BUDDY_BRIDGE_AUDIT", str(tmp_path / "audit.jsonl"))
    assert default_path() == tmp_path / "audit.jsonl"


def test_record_appends_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path=path)
    log.record(
        session_id="abcdef1234567890",
        tool_name="Bash",
        hint="git push origin main",
        matcher="ask",
        decision="allow",
        source="stick",
        elapsed_s=2.345,
    )
    log.record(
        session_id="ffff",
        tool_name="Bash",
        hint="ls",
        matcher="allow",
        decision="allow",
        source="auto_allow",
    )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    a, b = json.loads(lines[0]), json.loads(lines[1])
    # Session id is truncated to first 8 chars for readability.
    assert a["session"] == "abcdef12"
    assert a["tool"] == "Bash"
    assert a["hint"] == "git push origin main"
    assert a["matcher"] == "ask"
    assert a["decision"] == "allow"
    assert a["source"] == "stick"
    assert a["elapsed_s"] == 2.345
    assert "ts" in a
    # auto_allow path omits elapsed_s.
    assert "elapsed_s" not in b


def test_record_long_hint_is_truncated(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path=path)
    long_hint = "x" * 500
    log.record(
        session_id="x", tool_name="Bash", hint=long_hint,
        matcher="ask", decision="deny", source="stick",
    )
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert len(entry["hint"]) == 200


def test_record_creates_parent_dir(tmp_path):
    path = tmp_path / "deep" / "nested" / "audit.jsonl"
    log = AuditLog(path=path)
    log.record(
        session_id="x", tool_name="Bash", hint="ls",
        matcher="allow", decision="allow", source="auto_allow",
    )
    assert path.exists()


def test_record_swallows_io_errors(tmp_path):
    """Daemon must never crash on audit IO failure."""
    path = tmp_path / "audit.jsonl"
    path.parent.chmod(0o500)  # remove write perm
    log = AuditLog(path=path)
    try:
        # Should not raise.
        log.record(
            session_id="x", tool_name="Bash", hint="ls",
            matcher="allow", decision="allow", source="auto_allow",
        )
        log.record(
            session_id="x", tool_name="Bash", hint="ls",
            matcher="allow", decision="allow", source="auto_allow",
        )
        # Sticky "failure logged" flag suppresses repeated warnings.
        assert log._failure_logged
    finally:
        path.parent.chmod(0o700)  # restore so pytest cleanup works


def test_record_decision_can_be_none(tmp_path):
    """defer / ble_disconnected paths return no decision."""
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path=path)
    log.record(
        session_id="x", tool_name="Bash", hint="some-tool",
        matcher="default", decision=None, source="defer",
    )
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["decision"] is None
    assert entry["source"] == "defer"
