"""Append-only JSONL log of every PreToolUse decision, plus a CLI viewer.

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
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

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


# ---- viewer (powers `cc-buddy-bridge audit` subcommand) ----

# ANSI colour escapes; suppressed by `--ascii` or when stdout isn't a tty.
_ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[2m"
_ANSI_RED = "\033[31m"
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"


def iter_entries(
    path: Optional[Path] = None,
    *,
    decision: Optional[str] = None,
    source: Optional[str] = None,
    tool: Optional[str] = None,
) -> Iterator[dict[str, Any]]:
    """Stream parsed entries from the audit file in chronological order.

    Bad lines (JSONDecodeError) are silently skipped — never crash the viewer
    on a partially-written record at the tail.
    """
    target = path or default_path()
    if not target.exists():
        return
    with target.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if decision is not None and entry.get("decision") != decision:
                continue
            if source is not None and entry.get("source") != source:
                continue
            if tool is not None and entry.get("tool") != tool:
                continue
            yield entry


def _fmt_decision(dec: Optional[str], *, ascii_only: bool) -> str:
    label = (dec or "—")[:5]
    pad = f"{label:<5}"
    if ascii_only:
        return pad
    if dec == "allow":
        return f"{_ANSI_GREEN}{pad}{_ANSI_RESET}"
    if dec == "deny":
        return f"{_ANSI_RED}{pad}{_ANSI_RESET}"
    return f"{_ANSI_DIM}{pad}{_ANSI_RESET}"


def _fmt_source(src: str, *, ascii_only: bool) -> str:
    pad = f"{src[:11]:<11}"
    if ascii_only:
        return pad
    if src == "stick":
        return f"{_ANSI_YELLOW}{pad}{_ANSI_RESET}"
    if src == "timeout":
        return f"{_ANSI_RED}{pad}{_ANSI_RESET}"
    return f"{_ANSI_DIM}{pad}{_ANSI_RESET}"


def format_entry(entry: dict[str, Any], *, ascii_only: bool = False, width: int = 120) -> str:
    """One-line aligned rendering. Hint truncates to fill the remaining width."""
    ts = entry.get("ts") or ""
    # ts is "YYYY-MM-DDTHH:MM:SS.mmm±HH:MM" — slice out the HH:MM:SS.mmm part.
    time_str = ts.split("T", 1)[1][:12] if "T" in ts else ts[:12]
    tool = (entry.get("tool") or "")[:8]
    dec_segment = _fmt_decision(entry.get("decision"), ascii_only=ascii_only)
    src_segment = _fmt_source(entry.get("source") or "", ascii_only=ascii_only)
    hint = (entry.get("hint") or "").replace("\n", " ")

    # Available width for the hint = total - fixed columns - separators.
    # Columns: time(12) tool(8) decision(5) source(11) = 36 + 4 spaces = 40 chars.
    avail = max(20, width - 40)
    if len(hint) > avail:
        hint = hint[: avail - 1] + "…"

    return f"{time_str:<12} {tool:<8} {dec_segment} {src_segment} {hint}"


def _terminal_width(default: int = 120) -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return default


def render(
    *,
    path: Optional[Path] = None,
    last: int = 20,
    decision: Optional[str] = None,
    source: Optional[str] = None,
    tool: Optional[str] = None,
    ascii_only: bool = False,
    follow: bool = False,
    out=sys.stdout,
) -> int:
    """Power for the ``cc-buddy-bridge audit`` subcommand."""
    target = path or default_path()
    use_color = (not ascii_only) and out.isatty()
    width = _terminal_width()

    # Header.
    if use_color:
        out.write(f"{_ANSI_DIM}# audit log: {target}{_ANSI_RESET}\n")
    else:
        out.write(f"# audit log: {target}\n")

    if not target.exists():
        out.write("# (empty — no PreToolUse decisions recorded yet)\n")
        return 0

    # Tail to `last` entries by collecting all that pass the filter then slicing.
    # The file is bounded by daemon lifetime use, so slurping is fine even for
    # multi-week histories (a heavy day is ~hundreds of entries).
    entries = list(iter_entries(target, decision=decision, source=source, tool=tool))
    if last > 0 and len(entries) > last:
        entries = entries[-last:]
    for e in entries:
        out.write(format_entry(e, ascii_only=not use_color, width=width) + "\n")

    if not follow:
        return 0

    # Follow mode: poll the file for new bytes. Cross-platform (no inotify dep).
    # Resilient to file rotation: if size shrinks, reset to start.
    out.flush()
    try:
        with target.open(encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.5)
                    # Detect truncation (rotation, manual reset).
                    try:
                        size = target.stat().st_size
                    except OSError:
                        continue
                    if size < f.tell():
                        f.seek(0)
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if decision is not None and entry.get("decision") != decision:
                    continue
                if source is not None and entry.get("source") != source:
                    continue
                if tool is not None and entry.get("tool") != tool:
                    continue
                out.write(format_entry(entry, ascii_only=not use_color, width=width) + "\n")
                out.flush()
    except KeyboardInterrupt:
        return 0
