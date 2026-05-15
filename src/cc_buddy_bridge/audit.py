"""Append-only JSONL log of every PreToolUse decision.

Useful for "what did I let through last week?" forensics — especially when
running with ``defaultMode: bypassPermissions`` + ``strict = false`` where the
matcher silently auto-approves a lot of things.

One line per decision::

    {"ts":"2026-05-16T01:23:45.123+0800","session":"abc12345","tool":"Bash",
     "hint":"git push origin main","matcher":"ask","decision":"allow",
     "source":"stick","elapsed_s":2.3}

Fields:
  ts        ISO-8601 local timestamp with offset
  session   first 8 chars of the session id (full id is noisy)
  tool      tool name (Bash / Edit / Read / ...)
  hint      short summary of what's being run (command, file path, ...)
  matcher   matcher classification: "allow" / "ask" / "default"
  decision  what the bridge actually returned: "allow" / "deny" / null
  source    how we arrived at the decision:
              "auto_allow" — matcher short-circuited to allow
              "stick"      — user pressed A/B on the buddy
              "timeout"    — stick didn't respond within PERMISSION_WAIT_SECS
              "defer"      — bridge returned no opinion (Claude Code's flow ran)
  elapsed_s elapsed seconds for the round-trip (omitted on short-circuits)

Append failures are logged once and don't propagate; the daemon must never
crash because of audit IO.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


def default_path() -> Path:
    """Per-platform audit log location, matching the daemon log conventions."""
    override = os.environ.get("CC_BUDDY_BRIDGE_AUDIT")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "cc-buddy-bridge-audit.jsonl"
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "cc-buddy-bridge" / "audit.jsonl"
    # Linux / other Unix: XDG_DATA_HOME convention.
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "cc-buddy-bridge" / "audit.jsonl"


class AuditLog:
    """Append-only JSONL recorder.

    Stateless beyond the cached path and a "ever failed?" sticky flag so we
    don't spam logs with the same IOError on every PreToolUse.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or default_path()
        self._failure_logged = False

    def record(
        self,
        *,
        session_id: str,
        tool_name: str,
        hint: str,
        matcher: str,
        decision: Optional[str],
        source: str,
        elapsed_s: Optional[float] = None,
    ) -> None:
        entry: dict[str, Any] = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "session": (session_id or "")[:8],
            "tool": tool_name or "",
            "hint": (hint or "")[:200],
            "matcher": matcher,
            "decision": decision,
            "source": source,
        }
        if elapsed_s is not None:
            entry["elapsed_s"] = round(elapsed_s, 3)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            if not self._failure_logged:
                log.warning("audit log write failed (will not retry-log): %s: %s", self.path, e)
                self._failure_logged = True
