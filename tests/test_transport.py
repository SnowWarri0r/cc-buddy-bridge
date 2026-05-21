"""Unit tests for transport.py — spec parsing, platform dispatch, TCP e2e."""

from __future__ import annotations

import asyncio
import socket
import sys

import pytest

from cc_buddy_bridge import transport

# ---------------------------------------------------------------------------
# parse_spec
# ---------------------------------------------------------------------------


def test_parse_spec_tcp_with_host():
    t = transport.parse_spec("127.0.0.1:48765")
    assert isinstance(t, transport.TcpLoopbackTransport)
    assert t.host == "127.0.0.1"
    assert t.port == 48765
    assert t.address == "127.0.0.1:48765"


def test_parse_spec_tcp_host_omitted_uses_default():
    t = transport.parse_spec(":9000")
    assert isinstance(t, transport.TcpLoopbackTransport)
    assert t.host == transport.DEFAULT_TCP_HOST
    assert t.port == 9000


def test_parse_spec_rejects_bad_port():
    with pytest.raises(ValueError):
        transport.parse_spec("127.0.0.1:99999")


def test_parse_spec_empty_rejected():
    with pytest.raises(ValueError):
        transport.parse_spec("")


@pytest.mark.skipif(sys.platform == "win32", reason="Unix paths only valid off-Windows")
def test_parse_spec_unix_path_off_windows():
    t = transport.parse_spec("/tmp/foo.sock")
    assert isinstance(t, transport.UnixTransport)
    assert t.path == "/tmp/foo.sock"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only fail-fast check")
def test_parse_spec_unix_path_rejected_on_windows():
    with pytest.raises(ValueError, match="not supported on Windows"):
        transport.parse_spec("/tmp/foo.sock")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive letter paths")
def test_parse_spec_drive_letter_path_rejected_on_windows():
    # "C:\\foo\\bar.sock" — must NOT be misparsed as host="C", port=invalid
    with pytest.raises(ValueError):
        transport.parse_spec("C:\\Users\\me\\bridge.sock")


# ---------------------------------------------------------------------------
# default_spec / make_transport
# ---------------------------------------------------------------------------


def test_default_spec_matches_platform():
    spec = transport.default_spec()
    if sys.platform == "win32":
        assert spec == f"{transport.DEFAULT_TCP_HOST}:{transport.DEFAULT_TCP_PORT}"
    else:
        assert spec == transport.DEFAULT_UNIX_PATH


def test_make_transport_explicit_spec_wins(monkeypatch):
    monkeypatch.setenv("CC_BUDDY_BRIDGE_SOCK", ":1111")
    t = transport.make_transport(":2222")
    assert isinstance(t, transport.TcpLoopbackTransport)
    assert t.port == 2222


def test_make_transport_env_used_when_no_arg(monkeypatch):
    monkeypatch.setenv("CC_BUDDY_BRIDGE_SOCK", ":3333")
    t = transport.make_transport(None)
    assert isinstance(t, transport.TcpLoopbackTransport)
    assert t.port == 3333


def test_make_transport_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("CC_BUDDY_BRIDGE_SOCK", raising=False)
    t = transport.make_transport(None)
    if sys.platform == "win32":
        assert isinstance(t, transport.TcpLoopbackTransport)
    else:
        assert isinstance(t, transport.UnixTransport)


# ---------------------------------------------------------------------------
# UnixTransport class-level guards
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only constructor guard")
def test_unix_transport_rejects_on_windows():
    with pytest.raises(RuntimeError):
        transport.UnixTransport("/tmp/x.sock")


# ---------------------------------------------------------------------------
# TcpLoopbackTransport: end-to-end (works on every OS)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_tcp_transport_address_format():
    t = transport.TcpLoopbackTransport(host="127.0.0.1", port=12345)
    assert t.address == "127.0.0.1:12345"


def test_tcp_transport_is_in_use_false_on_unbound_port():
    port = _free_port()
    t = transport.TcpLoopbackTransport(port=port)
    assert t.is_in_use() is False


def test_tcp_transport_cleanup_stale_is_noop():
    # Should not raise even when nothing exists.
    transport.TcpLoopbackTransport(port=_free_port()).cleanup_stale()


def test_tcp_transport_server_client_roundtrip():
    """Spin up a server, connect with sync_connect, exchange one message."""
    port = _free_port()
    t = transport.TcpLoopbackTransport(port=port)

    async def run() -> str:
        received: list[bytes] = []

        async def on_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            data = await reader.readline()
            received.append(data)
            writer.write(b"pong\n")
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

        server = await t.start_server(on_conn)
        try:
            # Synchronous client (in a thread so we don't block the loop).
            loop = asyncio.get_running_loop()

            def client() -> bytes:
                s = t.sync_connect(timeout=2.0)
                try:
                    s.sendall(b"ping\n")
                    chunks = b""
                    while b"\n" not in chunks:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        chunks += chunk
                    return chunks
                finally:
                    s.close()

            reply = await loop.run_in_executor(None, client)
        finally:
            server.close()
            await server.wait_closed()

        assert received == [b"ping\n"]
        return reply.decode("utf-8").strip()

    result = asyncio.run(run())
    assert result == "pong"


def test_tcp_transport_is_in_use_true_when_server_listening():
    """Verify is_in_use returns True with a listening server (separate test —
    a probe connection counts as a real client, so don't mix it into the
    roundtrip test where it pollutes the received-buffer assertion."""
    port = _free_port()
    t = transport.TcpLoopbackTransport(port=port)

    async def run() -> bool:
        async def on_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            # Just drain and close; the probe doesn't send anything.
            await reader.read(4096)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

        server = await t.start_server(on_conn)
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, t.is_in_use)
        finally:
            server.close()
            await server.wait_closed()

    assert asyncio.run(run()) is True


# ---------------------------------------------------------------------------
# UnixTransport: end-to-end (POSIX only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="UnixTransport is POSIX-only")
def test_unix_transport_server_client_roundtrip(tmp_path):
    sock_path = str(tmp_path / "bridge.sock")
    t = transport.UnixTransport(path=sock_path)

    async def run() -> str:
        received: list[bytes] = []

        async def on_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            data = await reader.readline()
            received.append(data)
            writer.write(b"pong\n")
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

        server = await t.start_server(on_conn)
        try:
            loop = asyncio.get_running_loop()

            def client() -> bytes:
                s = t.sync_connect(timeout=2.0)
                try:
                    s.sendall(b"ping\n")
                    chunks = b""
                    while b"\n" not in chunks:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        chunks += chunk
                    return chunks
                finally:
                    s.close()

            reply = await loop.run_in_executor(None, client)
        finally:
            server.close()
            await server.wait_closed()
            t.cleanup_stale()

        assert received == [b"ping\n"]
        return reply.decode("utf-8").strip()

    result = asyncio.run(run())
    assert result == "pong"


@pytest.mark.skipif(sys.platform == "win32", reason="UnixTransport is POSIX-only")
def test_unix_transport_cleanup_removes_stale_file(tmp_path):
    sock_path = tmp_path / "stale.sock"
    sock_path.write_bytes(b"")  # leftover file
    t = transport.UnixTransport(path=str(sock_path))
    # No server is listening → stale file should be cleaned up by is_in_use.
    assert t.is_in_use() is False
    assert not sock_path.exists()
