"""PreToolUse hook — blocks Claude Code's tool call until the stick's button decides.

If the daemon is unreachable or BLE is not connected, emits no decision so that
Claude Code's normal approval flow runs.

stdin: { session_id, tool_name, tool_input, tool_use_id, ... }
stdout (on decision): { "hookSpecificOutput": { "hookEventName": "PreToolUse",
                                                 "permissionDecision": "allow"|"deny"|"ask" } }
"""

from __future__ import annotations

import json
import sys

from ._client import post, read_hook_input

# Hard upper bound for how long this hook blocks. Must be < the `timeout` we
# set in settings.json and < daemon's PERMISSION_WAIT_SECS. 5 minutes is plenty
# of human reaction time and still leaves headroom.
BLOCK_TIMEOUT_SECS = 320.0


def _summarize(tool_input: object) -> str:
    """Short human-readable hint from a tool_input dict."""
    if isinstance(tool_input, dict):
        # Bash: command; Edit/Write: file_path; fallback: first string value.
        for key in ("command", "file_path", "path", "url"):
            v = tool_input.get(key)
            if isinstance(v, str) and v:
                return v
        for v in tool_input.values():
            if isinstance(v, str) and v:
                return v
    if isinstance(tool_input, str):
        return tool_input
    return ""


def main() -> int:
    payload = read_hook_input()
    event = {
        "evt": "pretooluse",
        "session_id": payload.get("session_id", ""),
        "tool_use_id": payload.get("tool_use_id", ""),
        "tool_name": payload.get("tool_name", ""),
        "hint": _summarize(payload.get("tool_input")),
        "cwd": payload.get("cwd", ""),
    }
    resp = post(event, timeout=BLOCK_TIMEOUT_SECS)
    if resp is None or not resp.get("ok"):
        # Daemon unreachable or errored — defer to Claude Code's default behavior.
        return 0
    decision = resp.get("decision")
    if decision not in ("allow", "deny", "ask"):
        return 0
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    sys.stdout.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
