"""Installer tests — run against a temp settings.json so we never touch the real one."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_buddy_bridge import installer


@pytest.fixture
def temp_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "settings.json"
    monkeypatch.setattr(installer, "SETTINGS_PATH", p)
    return p


def _baseline(extra: dict | None = None) -> dict:
    d = {
        "statusLine": {"type": "command", "command": "true"},
        "permissions": {"defaultMode": "auto"},
    }
    if extra:
        d.update(extra)
    return d


def _write(p: Path, data: dict) -> None:
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_install_from_scratch(temp_settings: Path) -> None:
    _write(temp_settings, _baseline())
    assert installer.install_hooks() == 0
    data = json.loads(temp_settings.read_text())
    assert "hooks" in data
    # All 6 hook events covered.
    assert set(data["hooks"].keys()) == {
        "PreToolUse", "PostToolUse", "SessionStart", "SessionEnd",
        "UserPromptSubmit", "Stop",
    }
    # Non-hook settings preserved.
    assert data["statusLine"]["command"] == "true"
    assert data["permissions"]["defaultMode"] == "auto"


def test_install_is_idempotent(temp_settings: Path) -> None:
    _write(temp_settings, _baseline())
    installer.install_hooks()
    first = json.loads(temp_settings.read_text())
    installer.install_hooks()
    second = json.loads(temp_settings.read_text())
    # Same number of entries — no duplicates.
    assert len(first["hooks"]["PreToolUse"][0]["hooks"]) == len(second["hooks"]["PreToolUse"][0]["hooks"]) == 1


def test_uninstall_removes_only_our_entries(temp_settings: Path) -> None:
    baseline = _baseline({
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "/path/to/unrelated-hook", "timeout": 5},
                    ],
                }
            ]
        }
    })
    _write(temp_settings, baseline)
    installer.install_hooks()

    # Now install added our Bash hook alongside the user's.
    after_install = json.loads(temp_settings.read_text())
    bash_group = after_install["hooks"]["PreToolUse"][0]
    assert bash_group["matcher"] == "Bash"
    assert len(bash_group["hooks"]) == 2

    assert installer.uninstall_hooks() == 0
    after_uninstall = json.loads(temp_settings.read_text())
    # User's unrelated hook survived.
    bash_group = after_uninstall["hooks"]["PreToolUse"][0]
    assert len(bash_group["hooks"]) == 1
    assert bash_group["hooks"][0]["command"] == "/path/to/unrelated-hook"


def test_uninstall_drops_empty_hooks_block(temp_settings: Path) -> None:
    _write(temp_settings, _baseline())
    installer.install_hooks()
    installer.uninstall_hooks()
    data = json.loads(temp_settings.read_text())
    # Nothing else was in hooks; block should be gone.
    assert "hooks" not in data


def test_uninstall_when_nothing_to_remove(temp_settings: Path) -> None:
    _write(temp_settings, _baseline())
    assert installer.uninstall_hooks() == 0  # no-op, exit clean
