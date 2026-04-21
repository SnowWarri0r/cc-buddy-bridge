"""Stop hook — turn finished, clear running flag."""

from __future__ import annotations

from ._client import post, read_hook_input


def main() -> int:
    payload = read_hook_input()
    post({
        "evt": "turn_end",
        "session_id": payload.get("session_id", ""),
        "transcript_path": payload.get("transcript_path", ""),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
