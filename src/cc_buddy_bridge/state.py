"""In-memory aggregated state across all live Claude Code sessions."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class PendingPermission:
    """A tool call waiting on a stick-button decision."""
    tool_use_id: str
    tool_name: str
    hint: str
    session_id: str
    issued_at: float  # monotonic seconds


@dataclass
class Session:
    session_id: str
    started_at: float
    transcript_path: Optional[str] = None
    cwd: Optional[str] = None
    running: bool = False
    pending: Optional[PendingPermission] = None


@dataclass
class Entry:
    """One transcript-style line surfaced on the stick."""
    at: float  # wall-clock epoch seconds (for formatting HH:MM)
    text: str


class State:
    """Aggregator. Hook handlers mutate this via the public methods below."""

    MAX_ENTRIES = 8  # stick's display can only hold a few

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.entries: list[Entry] = []
        self.tokens_cumulative: int = 0
        self.tokens_today: int = 0
        self.tokens_day_key: str = _today_key()
        # Monotonic timestamp until which heartbeats should carry
        # ``completed: true`` — the firmware's celebrate-animation trigger.
        # Pulsed by turn_end, observed by build_heartbeat.
        self.completed_until: float = 0.0

    # ---- session lifecycle ----

    def session_start(self, session_id: str, transcript_path: str | None = None, cwd: str | None = None) -> None:
        if session_id not in self.sessions:
            self.sessions[session_id] = Session(
                session_id=session_id,
                started_at=time.time(),
                transcript_path=transcript_path,
                cwd=cwd,
            )

    def session_end(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    # ---- turn lifecycle ----

    def turn_begin(self, session_id: str) -> None:
        s = self.sessions.get(session_id)
        if s is not None:
            s.running = True

    def turn_end(self, session_id: str) -> None:
        s = self.sessions.get(session_id)
        if s is not None:
            s.running = False

    # ---- permission lifecycle ----

    def permission_pending(
        self,
        session_id: str,
        tool_use_id: str,
        tool_name: str,
        hint: str,
    ) -> PendingPermission:
        p = PendingPermission(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            hint=hint,
            session_id=session_id,
            issued_at=time.monotonic(),
        )
        s = self.sessions.get(session_id)
        if s is None:
            s = Session(session_id=session_id, started_at=time.time())
            self.sessions[session_id] = s
        s.pending = p
        return p

    def permission_resolved(self, tool_use_id: str) -> Optional[PendingPermission]:
        """Clear the pending permission with this tool_use_id across any session. Returns it."""
        for s in self.sessions.values():
            if s.pending is not None and s.pending.tool_use_id == tool_use_id:
                p = s.pending
                s.pending = None
                return p
        return None

    def find_pending_by_id(self, tool_use_id: str) -> Optional[PendingPermission]:
        for s in self.sessions.values():
            if s.pending is not None and s.pending.tool_use_id == tool_use_id:
                return s.pending
        return None

    def first_pending(self) -> Optional[PendingPermission]:
        """Oldest pending permission across sessions (for stick's single-prompt display)."""
        pendings = [s.pending for s in self.sessions.values() if s.pending is not None]
        if not pendings:
            return None
        return min(pendings, key=lambda p: p.issued_at)

    # ---- celebrate pulse ----

    def pulse_completed(self, duration_secs: float = 5.0) -> None:
        """Make subsequent heartbeats include ``completed: true`` for a few
        seconds — firmware reads that field as the celebrate-animation
        trigger (data.h:_applyJson → main.cpp:derive)."""
        self.completed_until = time.monotonic() + duration_secs

    @property
    def is_celebrating(self) -> bool:
        return time.monotonic() < self.completed_until

    # ---- entries ----

    def add_entry(self, text: str, at: Optional[float] = None) -> None:
        text = text.strip()
        if not text:
            return
        self.entries.insert(0, Entry(at=at if at is not None else time.time(), text=text))
        del self.entries[self.MAX_ENTRIES:]

    # ---- tokens ----

    def set_tokens(self, cumulative: int, today: int) -> None:
        """Called by the JSONL tailer after recomputing from transcript files."""
        day = _today_key()
        if day != self.tokens_day_key:
            self.tokens_day_key = day
        self.tokens_cumulative = cumulative
        self.tokens_today = today

    # ---- aggregates ----

    @property
    def total(self) -> int:
        return len(self.sessions)

    @property
    def running_count(self) -> int:
        return sum(1 for s in self.sessions.values() if s.running)

    @property
    def waiting_count(self) -> int:
        return sum(1 for s in self.sessions.values() if s.pending is not None)


def _today_key() -> str:
    return datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d")
