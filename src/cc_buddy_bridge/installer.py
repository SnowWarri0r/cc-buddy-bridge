"""Install / uninstall cc-buddy-bridge hooks into ~/.claude/settings.json.

We identify our entries by a marker substring in the command string
(`cc_buddy_bridge.hooks.`). Non-cc-buddy-bridge hooks are left alone.
"""

from __future__ import annotations

import json
import shutil
import sys
import sysconfig
from datetime import datetime
from pathlib import Path
from typing import Any

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
MARKER = "cc_buddy_bridge.hooks."
HOOK_TIMEOUT_SECS = 330  # must be > daemon's PERMISSION_WAIT_SECS + a small buffer

# (Claude Code hook event name, python module, matcher, needs_decision)
HOOK_DEFS: list[tuple[str, str, str | None, bool]] = [
    ("PreToolUse",        "cc_buddy_bridge.hooks.pretooluse",         "Bash", True),
    ("PostToolUse",       "cc_buddy_bridge.hooks.posttooluse",        "*",    False),
    ("SessionStart",      "cc_buddy_bridge.hooks.session_start",      None,   False),
    ("SessionEnd",        "cc_buddy_bridge.hooks.session_end",        None,   False),
    ("UserPromptSubmit",  "cc_buddy_bridge.hooks.user_prompt_submit", None,   False),
    ("Stop",              "cc_buddy_bridge.hooks.stop",               None,   False),
]


def _python_executable() -> str:
    """Prefer the Python that was used to install this package (keeps things working
    when invoked via `cc-buddy-bridge install` from a venv)."""
    return sys.executable


def _hook_command(module: str) -> str:
    return f"{_python_executable()} -m {module}"


def _is_our_entry(hook_obj: dict[str, Any]) -> bool:
    cmd = hook_obj.get("command", "")
    return isinstance(cmd, str) and MARKER in cmd


def _load_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    with SETTINGS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_settings(data: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _backup() -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = SETTINGS_PATH.with_name(f"settings.json.ccbb-backup-{ts}")
    shutil.copy2(SETTINGS_PATH, dest)
    return dest


def install_hooks() -> int:
    if not SETTINGS_PATH.exists():
        print(f"settings.json not found at {SETTINGS_PATH}", file=sys.stderr)
        return 2

    backup = _backup()
    print(f"backed up settings to {backup}")

    data = _load_settings()
    hooks = data.setdefault("hooks", {})

    added = 0
    for event, module, matcher, needs_decision in HOOK_DEFS:
        entries = hooks.setdefault(event, [])
        cmd = _hook_command(module)

        # Find or create the matcher group.
        group = _find_matcher_group(entries, matcher)
        if group is None:
            group = {"hooks": []}
            if matcher is not None:
                group["matcher"] = matcher
            entries.append(group)

        inner = group.setdefault("hooks", [])
        # Skip if an identical cc-buddy-bridge entry already exists.
        if any(_is_our_entry(h) and h.get("command") == cmd for h in inner):
            continue

        # Remove any stale cc-buddy-bridge entry for the same module (path changed, etc.).
        inner[:] = [
            h for h in inner
            if not (_is_our_entry(h) and module in h.get("command", ""))
        ]
        inner.append({
            "type": "command",
            "command": cmd,
            "timeout": HOOK_TIMEOUT_SECS if needs_decision else 10,
        })
        added += 1

    _save_settings(data)
    print(f"installed {added} hook(s) into {SETTINGS_PATH}")
    if added == 0:
        print("(already up to date)")
    return 0


def uninstall_hooks() -> int:
    if not SETTINGS_PATH.exists():
        print(f"settings.json not found at {SETTINGS_PATH}", file=sys.stderr)
        return 2

    data = _load_settings()
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        print("no hooks block — nothing to remove")
        return 0

    removed = 0
    for event in list(hooks.keys()):
        entries = hooks.get(event) or []
        if not isinstance(entries, list):
            continue
        cleaned_groups = []
        for group in entries:
            if not isinstance(group, dict):
                cleaned_groups.append(group)
                continue
            inner = group.get("hooks") or []
            before = len(inner)
            remaining = [h for h in inner if not _is_our_entry(h)]
            removed += before - len(remaining)
            if remaining:
                group["hooks"] = remaining
                cleaned_groups.append(group)
            # else drop the group entirely
        if cleaned_groups:
            hooks[event] = cleaned_groups
        else:
            hooks.pop(event)

    if not hooks:
        data.pop("hooks", None)

    if removed == 0:
        print("no cc-buddy-bridge hooks found — nothing to remove")
        return 0

    backup = _backup()
    print(f"backed up settings to {backup}")
    _save_settings(data)
    print(f"removed {removed} hook(s) from {SETTINGS_PATH}")
    return 0


def show_status() -> int:
    print("Hooks:")
    if not SETTINGS_PATH.exists():
        print(f"  settings.json not found at {SETTINGS_PATH}")
    else:
        data = _load_settings()
        hooks = data.get("hooks") or {}
        any_installed = False
        for event, entries in hooks.items():
            if not isinstance(entries, list):
                continue
            for group in entries:
                if not isinstance(group, dict):
                    continue
                for h in group.get("hooks") or []:
                    if _is_our_entry(h):
                        any_installed = True
                        matcher = group.get("matcher", "*")
                        print(f"  {event} [{matcher}] → {h.get('command')}")
        if not any_installed:
            print("  no cc-buddy-bridge hooks installed")

    print("\nService (macOS launchd):")
    try:
        from . import service
    except ImportError:
        print("  (service module unavailable)")
        return 0
    if not service.is_installed():
        print("  not installed (run `cc-buddy-bridge install --service` to add)")
    else:
        loaded = "loaded" if service.is_loaded() else "installed but not loaded"
        print(f"  {loaded}: {service.PLIST_PATH}")
        print(f"  logs: {service.LOG_PATH}")
    return 0


def _find_matcher_group(entries: list, matcher: str | None) -> dict | None:
    for e in entries:
        if not isinstance(e, dict):
            continue
        # Treat absent matcher and "*" as the same target for events with no matcher.
        if matcher is None and "matcher" not in e:
            return e
        if matcher is not None and e.get("matcher") == matcher:
            return e
    return None
