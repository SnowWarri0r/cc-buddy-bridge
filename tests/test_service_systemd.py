"""Tests for the Linux systemd backend — unit-file generation only.

Anything that shells out to ``systemctl`` is covered by manual integration
testing on a real Linux box (Ubuntu / Fedora) rather than a subprocess mock,
to match the launchd backend's testing style.
"""

from __future__ import annotations

import sys

from cc_buddy_bridge import _service_systemd


def _parse_unit(text: str) -> dict[str, dict[str, str]]:
    """Tiny INI-ish parser: section name -> {key: value}.

    systemd unit files are not strictly INI but for our generated output
    a section/key=value parse is enough.
    """
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        assert current is not None, f"key before any [Section]: {raw!r}"
        key, _, value = line.partition("=")
        current[key.strip()] = value.strip()
    return sections


def test_unit_has_required_sections():
    sections = _parse_unit(_service_systemd._build_unit())
    assert {"Unit", "Service", "Install"}.issubset(sections.keys())


def test_unit_description_is_set():
    sections = _parse_unit(_service_systemd._build_unit())
    assert "Description" in sections["Unit"]
    assert sections["Unit"]["Description"]


def test_unit_execstart_uses_current_interpreter():
    sections = _parse_unit(_service_systemd._build_unit())
    exec_start = sections["Service"]["ExecStart"]
    # Must invoke the same Python that's running the install (so the venv
    # carrying bleak/watchfiles is the one launched at boot).
    assert exec_start == f"{sys.executable} -m cc_buddy_bridge.cli daemon"


def test_unit_restarts_on_failure():
    sections = _parse_unit(_service_systemd._build_unit())
    assert sections["Service"]["Type"] == "simple"
    assert sections["Service"]["Restart"] == "on-failure"
    # Some delay so we don't hot-loop if BLE setup is permanently broken.
    assert int(sections["Service"]["RestartSec"]) >= 1


def test_unit_install_target_is_default():
    sections = _parse_unit(_service_systemd._build_unit())
    # default.target is correct for user units — graphical-session.target
    # only exists in a graphical session, which would gate out terminal-only
    # users.
    assert sections["Install"]["WantedBy"] == "default.target"


def test_unit_path_under_user_systemd_dir():
    p = _service_systemd.UNIT_PATH
    assert p.name == "cc-buddy-bridge.service"
    assert p.parent.parts[-3:] == (".config", "systemd", "user")


def test_log_path_points_at_journalctl():
    log_target = _service_systemd.log_path()
    assert isinstance(log_target, str)
    assert "journalctl" in log_target
    assert _service_systemd.UNIT_NAME in log_target
