"""Unit tests for service.py — plist generation only.

Anything that shells out to ``launchctl`` is covered by manual integration
testing (install/uninstall on a real Mac) rather than a subprocess mock, to
keep these tests honest about what actually ships.
"""

from __future__ import annotations

import plistlib
import sys

import pytest

from cc_buddy_bridge import service


def test_plist_parses_as_valid_plist():
    data = service._build_plist()
    parsed = plistlib.loads(data)
    assert isinstance(parsed, dict)


def test_plist_has_expected_keys():
    parsed = plistlib.loads(service._build_plist())
    # Required for a user LaunchAgent
    assert parsed["Label"] == service.LABEL
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] is True
    # Interactive is required for CoreBluetooth access from a GUI-session agent
    assert parsed["ProcessType"] == "Interactive"


def test_plist_program_arguments_point_at_current_interpreter():
    parsed = plistlib.loads(service._build_plist())
    args = parsed["ProgramArguments"]
    assert args[0] == sys.executable
    assert args[1:] == ["-m", "cc_buddy_bridge.cli", "daemon"]


def test_plist_log_paths_redirected():
    parsed = plistlib.loads(service._build_plist())
    assert parsed["StandardOutPath"] == str(service.LOG_PATH)
    assert parsed["StandardErrorPath"] == str(service.LOG_PATH)


def test_plist_env_has_path_and_home():
    parsed = plistlib.loads(service._build_plist())
    env = parsed["EnvironmentVariables"]
    assert "HOME" in env
    assert "PATH" in env and "/usr/bin" in env["PATH"]


@pytest.mark.skipif(sys.platform == "darwin", reason="non-macOS-only check")
def test_install_refuses_on_non_macos(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    rc = service.install_service()
    assert rc == 2
    err = capsys.readouterr().err
    assert "macOS-only" in err or "macOS only" in err
