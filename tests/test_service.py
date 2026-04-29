"""Tests for service.py — the platform dispatcher and the macOS launchd backend.

The launchd-specific tests cover plist generation only. Anything that shells
out to ``launchctl`` is covered by manual integration testing on a real Mac
rather than a subprocess mock, to keep these tests honest about what actually
ships.
"""

from __future__ import annotations

import plistlib
import sys

import pytest

from cc_buddy_bridge import _service_launchd, service


def test_plist_parses_as_valid_plist():
    data = _service_launchd._build_plist()
    parsed = plistlib.loads(data)
    assert isinstance(parsed, dict)


def test_plist_has_expected_keys():
    parsed = plistlib.loads(_service_launchd._build_plist())
    # Required for a user LaunchAgent
    assert parsed["Label"] == _service_launchd.LABEL
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] is True
    # Interactive is required for CoreBluetooth access from a GUI-session agent
    assert parsed["ProcessType"] == "Interactive"


def test_plist_program_arguments_point_at_current_interpreter():
    parsed = plistlib.loads(_service_launchd._build_plist())
    args = parsed["ProgramArguments"]
    assert args[0] == sys.executable
    assert args[1:] == ["-m", "cc_buddy_bridge.cli", "daemon"]


def test_plist_log_paths_redirected():
    parsed = plistlib.loads(_service_launchd._build_plist())
    assert parsed["StandardOutPath"] == str(_service_launchd.LOG_PATH)
    assert parsed["StandardErrorPath"] == str(_service_launchd.LOG_PATH)


def test_plist_env_has_path_and_home():
    parsed = plistlib.loads(_service_launchd._build_plist())
    env = parsed["EnvironmentVariables"]
    assert "HOME" in env
    assert "PATH" in env and "/usr/bin" in env["PATH"]


def test_install_refuses_on_unsupported_platform(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    rc = service.install_service()
    assert rc == 2
    err = capsys.readouterr().err
    assert "macOS and Linux" in err


def test_uninstall_refuses_on_unsupported_platform(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    rc = service.uninstall_service()
    assert rc == 2
    err = capsys.readouterr().err
    assert "macOS and Linux" in err


def test_backend_name_resolves_per_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert service.backend_name() == "launchd"
    monkeypatch.setattr(sys, "platform", "linux")
    assert service.backend_name() == "systemd"
    monkeypatch.setattr(sys, "platform", "win32")
    assert service.backend_name() is None


@pytest.mark.parametrize("platform", ["win32", "freebsd14"])
def test_is_installed_false_on_unsupported_platform(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    assert service.is_installed() is False
    assert service.is_loaded() is False
    assert service.unit_path() is None
    assert service.log_path() is None
