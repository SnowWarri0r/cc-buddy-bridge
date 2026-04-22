"""Stream a character-pack folder to the stick over BLE.

Implements the char_begin → file → chunk → file_end → char_end protocol
from REFERENCE.md "Folder push". Strictly request/ack: we wait for each
ack before sending the next packet because the firmware's UART RX buffer
is only ~256 bytes and its base64 decode buffer is 300 bytes.

Non-recursive, dotfiles skipped, 1.8 MB size cap per REFERENCE.md.
``manifest.json``'s ``"name"`` field (when present) overrides the folder
name for the ``char_begin`` message.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

# Raw bytes per chunk. Firmware's decode buffer is 300 bytes; we stay
# comfortably below that so a single malformed byte can't overflow.
CHUNK_SIZE = 200

# Size cap per REFERENCE.md §Folder push.
MAX_TOTAL_BYTES = 1_800_000

# Ack timeouts. Flash erase on the stick can take ~200 ms for large GIFs,
# so give each ack a generous window.
ACK_TIMEOUT_FAST = 5.0   # file, chunk, file_end
ACK_TIMEOUT_SLOW = 15.0  # char_begin (filesystem wipe), char_end (manifest parse + reload)


ProgressCallback = Callable[[int, int], Awaitable[None]]


def _enumerate_files(folder: Path) -> list[Path]:
    """Flat file listing, dotfiles skipped, deterministically ordered.
    Order matters because the firmware writes files one at a time and some
    might reference others (manifest.json conventionally first doesn't hurt)."""
    files = []
    for child in folder.iterdir():
        if not child.is_file():
            continue
        if child.name.startswith("."):
            continue
        files.append(child)
    # manifest.json first, then the rest alphabetically — mirrors what the
    # desktop app seems to prefer.
    files.sort(key=lambda p: (0 if p.name == "manifest.json" else 1, p.name))
    return files


def _pack_name(folder: Path) -> str:
    """``manifest.json``'s ``name`` field if valid; otherwise the folder's own
    basename. Matches REFERENCE.md behaviour."""
    manifest = folder / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return folder.name
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return folder.name


async def push_character(
    daemon,  # type: ignore[no-untyped-def]  — avoid circular import
    folder_path: str,
    *,
    on_progress: Optional[ProgressCallback] = None,
) -> dict[str, Any]:
    """Send ``folder_path`` to the connected stick. Returns a summary dict."""
    folder = Path(folder_path).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"not a directory: {folder}")

    files = _enumerate_files(folder)
    if not files:
        raise ValueError(f"no files to push in {folder}")

    total_bytes = sum(f.stat().st_size for f in files)
    if total_bytes > MAX_TOTAL_BYTES:
        raise ValueError(
            f"folder too large: {total_bytes:,} bytes > {MAX_TOTAL_BYTES:,} cap"
        )

    name = _pack_name(folder)
    log.info("folder_push: name=%r  files=%d  total=%d bytes", name, len(files), total_bytes)

    await _send_expect(daemon, {"cmd": "char_begin", "name": name, "total": total_bytes},
                       "char_begin", timeout=ACK_TIMEOUT_SLOW)

    bytes_pushed = 0
    for fp in files:
        size = fp.stat().st_size
        await _send_expect(daemon, {"cmd": "file", "path": fp.name, "size": size},
                           "file", timeout=ACK_TIMEOUT_FAST)

        with fp.open("rb") as fh:
            while True:
                piece = fh.read(CHUNK_SIZE)
                if not piece:
                    break
                b64 = base64.b64encode(piece).decode("ascii")
                await _send_expect(daemon, {"cmd": "chunk", "d": b64},
                                   "chunk", timeout=ACK_TIMEOUT_FAST)
                bytes_pushed += len(piece)
                if on_progress is not None:
                    await on_progress(bytes_pushed, total_bytes)

        await _send_expect(daemon, {"cmd": "file_end"},
                           "file_end", timeout=ACK_TIMEOUT_FAST)
        log.info("folder_push: %s done (%d/%d bytes total)",
                 fp.name, bytes_pushed, total_bytes)

    await _send_expect(daemon, {"cmd": "char_end"},
                       "char_end", timeout=ACK_TIMEOUT_SLOW)
    log.info("folder_push: char_end acked — stick has switched to %r", name)

    return {
        "name": name,
        "files": len(files),
        "total_bytes": total_bytes,
    }


async def _send_expect(daemon, payload: dict, ack_type: str, *, timeout: float) -> dict[str, Any]:
    """Write one line over BLE and block until a matching ack arrives."""
    ok = await daemon.ble.send(payload)
    if not ok:
        raise RuntimeError(f"ble write failed for cmd:{payload.get('cmd')}")
    ack = await daemon.wait_for_ack(ack_type, timeout=timeout)
    if not ack.get("ok"):
        err = ack.get("error") or "no detail"
        raise RuntimeError(f"{ack_type} rejected: {err}")
    return ack
