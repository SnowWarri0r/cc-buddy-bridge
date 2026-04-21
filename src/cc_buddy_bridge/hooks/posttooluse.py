"""PostToolUse hook — fire-and-forget notice that a tool call finished."""

from __future__ import annotations

from ._client import post, read_hook_input


def main() -> int:
    payload = read_hook_input()
    post({
        "evt": "posttooluse",
        "session_id": payload.get("session_id", ""),
        "tool_use_id": payload.get("tool_use_id", ""),
        "tool_name": payload.get("tool_name", ""),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
