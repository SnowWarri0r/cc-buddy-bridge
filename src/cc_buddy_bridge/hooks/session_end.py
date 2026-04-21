"""SessionEnd hook — tell the daemon this session is gone."""

from __future__ import annotations

from ._client import post, read_hook_input


def main() -> int:
    payload = read_hook_input()
    post({
        "evt": "session_end",
        "session_id": payload.get("session_id", ""),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
