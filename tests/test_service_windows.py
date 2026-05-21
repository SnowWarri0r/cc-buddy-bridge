from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from cc_buddy_bridge import _service_windows


def test_constants_are_sane():
    assert _service_windows.NAME == "task-scheduler"
    assert _service_windows.TASK_NAME == "cc-buddy-bridge-daemon"
    # Logs live under the user's local AppData, not the project tree.
    assert isinstance(_service_windows.LOG_PATH, Path)
    assert "AppData" in str(_service_windows.LOG_PATH)


def test_install_creates_onlogon_task(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(_service_windows.shutil, "which", lambda _: "schtasks.exe")
    # Redirect the log path so the test never touches the real ~/AppData tree.
    fake_log = tmp_path / "AppData" / "Local" / "cc-buddy-bridge" / "daemon.log"
    monkeypatch.setattr(_service_windows, "LOG_PATH", fake_log)
    monkeypatch.setattr(sys, "executable", r"C:\Python\python.exe")

    calls: list[list[str]] = []

    def fake_run(args, capture_output, text):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_service_windows.subprocess, "run", fake_run)

    rc = _service_windows.install()

    assert rc == 0
    assert len(calls) == 1
    args = calls[0]
    # Verify the canonical /create invocation is intact.
    assert args[0] == "schtasks"
    assert "/create" in args
    assert "/tn" in args
    assert _service_windows.TASK_NAME in args
    assert "/sc" in args
    assert "onlogon" in args
    assert "/f" in args
    # /tr value should embed the current python interpreter and pipe to the log.
    tr_index = args.index("/tr")
    tr_value = args[tr_index + 1]
    assert "python.exe" in tr_value
    assert "cc_buddy_bridge.cli" in tr_value
    assert "daemon" in tr_value
    assert str(fake_log) in tr_value
    # Side effect: the log directory should be created.
    assert fake_log.parent.is_dir()
    out = capsys.readouterr().out
    assert "Task Scheduler task" in out


def test_install_fails_without_schtasks(monkeypatch, capsys):
    monkeypatch.setattr(_service_windows.shutil, "which", lambda _: None)
    rc = _service_windows.install()
    assert rc == 2
    err = capsys.readouterr().err
    assert "schtasks" in err


def test_uninstall_short_circuits_when_not_installed(monkeypatch, capsys):
    monkeypatch.setattr(_service_windows, "is_installed", lambda: False)
    # Should not call schtasks at all.
    def boom(*a, **kw):
        raise AssertionError("subprocess.run should not be called")
    monkeypatch.setattr(_service_windows.subprocess, "run", boom)

    rc = _service_windows.uninstall()
    assert rc == 0
    assert "nothing to do" in capsys.readouterr().out


def test_uninstall_runs_schtasks_delete(monkeypatch, capsys):
    monkeypatch.setattr(_service_windows, "is_installed", lambda: True)
    calls: list[list[str]] = []

    def fake_run(args, capture_output, text):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_service_windows.subprocess, "run", fake_run)
    rc = _service_windows.uninstall()
    assert rc == 0
    assert calls == [["schtasks", "/delete", "/tn", _service_windows.TASK_NAME, "/f"]]
    assert "removed" in capsys.readouterr().out


def test_is_installed_queries_task(monkeypatch):
    monkeypatch.setattr(_service_windows.shutil, "which", lambda _: "schtasks.exe")

    def fake_run(args, capture_output, text):
        assert args == ["schtasks", "/query", "/tn", _service_windows.TASK_NAME]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_service_windows.subprocess, "run", fake_run)
    assert _service_windows.is_installed() is True


def test_is_installed_false_when_query_returns_nonzero(monkeypatch):
    monkeypatch.setattr(_service_windows.shutil, "which", lambda _: "schtasks.exe")
    monkeypatch.setattr(
        _service_windows.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="not found"),
    )
    assert _service_windows.is_installed() is False


def test_is_installed_false_without_schtasks(monkeypatch):
    monkeypatch.setattr(_service_windows.shutil, "which", lambda _: None)
    assert _service_windows.is_installed() is False


def test_is_loaded_mirrors_is_installed(monkeypatch):
    monkeypatch.setattr(_service_windows, "is_installed", lambda: True)
    assert _service_windows.is_loaded() is True
    monkeypatch.setattr(_service_windows, "is_installed", lambda: False)
    assert _service_windows.is_loaded() is False


def test_unit_path_returns_task_name():
    s = _service_windows.unit_path()
    assert isinstance(s, str)
    assert _service_windows.TASK_NAME in s


def test_log_path_returns_log_constant():
    assert _service_windows.log_path() == _service_windows.LOG_PATH
