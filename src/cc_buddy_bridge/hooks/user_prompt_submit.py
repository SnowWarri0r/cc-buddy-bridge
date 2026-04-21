"""UserPromptSubmit hook — marks the session as running and surfaces the prompt."""

from __future__ import annotations

from ._client import post, read_hook_input


def main() -> int:
    payload = read_hook_input()
    post({
        "evt": "turn_begin",
        "session_id": payload.get("session_id", ""),
        "prompt": (payload.get("prompt") or "").strip(),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
