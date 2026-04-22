"""folder_push tests — uses a stub daemon so no BLE is touched."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from cc_buddy_bridge.folder_push import (
    CHUNK_SIZE,
    MAX_TOTAL_BYTES,
    _enumerate_files,
    _pack_name,
    push_character,
)


class StubDaemon:
    """Records everything written and answers each wait_for_ack with ok=True."""

    def __init__(self):
        self.sent: list[dict] = []
        self.ble = _StubBle(self)

    async def wait_for_ack(self, ack_type: str, timeout: float = 5.0) -> dict:
        # Fulfil the protocol: for chunk acks we return a running byte counter.
        if ack_type == "chunk":
            return {"ack": "chunk", "ok": True, "n": self._chunk_count() * CHUNK_SIZE}
        return {"ack": ack_type, "ok": True}

    def _chunk_count(self) -> int:
        return sum(1 for m in self.sent if m.get("cmd") == "chunk")


class _StubBle:
    def __init__(self, owner: StubDaemon):
        self._owner = owner

    async def send(self, obj: dict) -> bool:
        self._owner.sent.append(obj)
        return True


# ---- helpers -------------------------------------------------------------

def _make_pack(tmp_path: Path, files: dict[str, bytes], manifest_name: str | None = None) -> Path:
    folder = tmp_path / "pack"
    folder.mkdir()
    if manifest_name is not None:
        (folder / "manifest.json").write_text(json.dumps({"name": manifest_name}))
    for name, data in files.items():
        (folder / name).write_bytes(data)
    return folder


def _run(coro):
    return asyncio.run(coro)


# ---- unit: enumerate / name resolution -----------------------------------

def test_enumerate_skips_dotfiles(tmp_path):
    folder = _make_pack(tmp_path, {"a.gif": b"x", ".hidden": b"y"})
    files = _enumerate_files(folder)
    assert [f.name for f in files] == ["a.gif"]


def test_enumerate_puts_manifest_first(tmp_path):
    folder = _make_pack(tmp_path, {"z.gif": b"x", "b.gif": b"y"}, manifest_name="pet")
    files = _enumerate_files(folder)
    assert files[0].name == "manifest.json"
    assert [f.name for f in files[1:]] == ["b.gif", "z.gif"]


def test_pack_name_prefers_manifest(tmp_path):
    folder = _make_pack(tmp_path, {"x.gif": b"x"}, manifest_name="bufo")
    assert _pack_name(folder) == "bufo"


def test_pack_name_falls_back_to_folder(tmp_path):
    folder = _make_pack(tmp_path, {"x.gif": b"x"})
    assert _pack_name(folder) == "pack"


def test_pack_name_ignores_bad_manifest(tmp_path):
    folder = tmp_path / "weird"
    folder.mkdir()
    (folder / "manifest.json").write_text("not json at all")
    (folder / "x.gif").write_bytes(b"x")
    assert _pack_name(folder) == "weird"


# ---- integration: full protocol sequence --------------------------------

def test_push_character_sends_canonical_sequence(tmp_path):
    data_a = b"a" * (CHUNK_SIZE + 50)  # 2 chunks
    data_b = b"b" * 10                  # 1 chunk
    folder = _make_pack(tmp_path, {"a.gif": data_a, "b.gif": data_b}, manifest_name="pet")

    daemon = StubDaemon()
    result = _run(push_character(daemon, str(folder)))

    assert result["name"] == "pet"
    assert result["files"] == 3  # manifest + a + b
    assert result["total_bytes"] == len(data_a) + len(data_b) + len(b'{"name": "pet"}')

    cmds = [m.get("cmd") for m in daemon.sent]
    # Starts with char_begin, ends with char_end
    assert cmds[0] == "char_begin"
    assert cmds[-1] == "char_end"
    # Three file/file_end bookends
    assert cmds.count("file") == 3
    assert cmds.count("file_end") == 3
    # Chunks for the two real gifs: ceil(CHUNK_SIZE+50 / CHUNK_SIZE) = 2, plus 1 for b, plus some for manifest
    assert cmds.count("chunk") >= 3


def test_push_character_chunks_are_valid_base64(tmp_path):
    original = b"\x00\x01\x02\xff" * 200
    folder = _make_pack(tmp_path, {"raw.bin": original}, manifest_name="p")
    daemon = StubDaemon()
    _run(push_character(daemon, str(folder)))
    # Concatenate all chunks for raw.bin — they should base64-decode back to original
    decoded = bytearray()
    collecting = False
    for m in daemon.sent:
        if m.get("cmd") == "file" and m.get("path") == "raw.bin":
            collecting = True
            continue
        if m.get("cmd") == "file_end" and collecting:
            break
        if collecting and m.get("cmd") == "chunk":
            decoded.extend(base64.b64decode(m["d"]))
    assert bytes(decoded) == original


def test_push_character_rejects_oversize(tmp_path):
    folder = tmp_path / "big"
    folder.mkdir()
    (folder / "huge.gif").write_bytes(b"x" * (MAX_TOTAL_BYTES + 1))
    with pytest.raises(ValueError, match="too large"):
        _run(push_character(StubDaemon(), str(folder)))


def test_push_character_rejects_non_dir(tmp_path):
    with pytest.raises(ValueError, match="not a directory"):
        _run(push_character(StubDaemon(), str(tmp_path / "nope")))


def test_push_character_rejects_empty(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    with pytest.raises(ValueError, match="no files"):
        _run(push_character(StubDaemon(), str(folder)))
