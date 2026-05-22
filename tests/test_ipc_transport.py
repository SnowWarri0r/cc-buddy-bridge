from __future__ import annotations

import pytest

from cc_buddy_bridge import ipc
from cc_buddy_bridge.hooks._client import post


def test_parse_tcp_loopback_spec() -> None:
    transport = ipc.parse_spec("127.0.0.1:48765")

    assert isinstance(transport, ipc.TcpLoopbackTransport)
    assert transport.address == "127.0.0.1:48765"


def test_parse_tcp_empty_host_uses_loopback() -> None:
    transport = ipc.parse_spec(":48765")

    assert isinstance(transport, ipc.TcpLoopbackTransport)
    assert transport.address == "127.0.0.1:48765"


def test_parse_tcp_rejects_non_loopback_host() -> None:
    with pytest.raises(ValueError, match="loopback"):
        ipc.parse_spec("0.0.0.0:48765")


def test_windows_default_is_tcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ipc.sys, "platform", "win32")

    assert ipc.default_spec() == "127.0.0.1:48765"


def test_windows_rejects_path_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ipc.sys, "platform", "win32")

    with pytest.raises(ValueError, match="Unix socket paths"):
        ipc.parse_spec(r"C:\tmp\cc-buddy-bridge.sock")


def test_make_transport_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_BUDDY_BRIDGE_SOCK", ":48766")

    transport = ipc.make_transport()

    assert isinstance(transport, ipc.TcpLoopbackTransport)
    assert transport.address == "127.0.0.1:48766"


def test_posix_default_is_unix_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ipc.sys, "platform", "linux")

    assert ipc.default_spec() == "/tmp/cc-buddy-bridge.sock"


def test_hook_post_returns_none_for_invalid_transport_spec() -> None:
    assert post({"evt": "session_start"}, socket_path="0.0.0.0:48765") is None
