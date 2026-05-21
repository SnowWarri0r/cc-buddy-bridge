"""Tests for the self-update entry point.

The shell-out paths (git pull, pip install, service restart) are
intentionally not exercised here — they're too OS-specific and side-
effectful. We test the helpers + the bail-out branches.
"""

from __future__ import annotations

from cc_buddy_bridge import update
from cc_buddy_bridge import version_check as vc


def _fake_info(current="0.1.0", latest=None, fetched_at=0.0):
    return vc.UpdateInfo(current=current, latest=latest, fetched_at=fetched_at)


# ---- package_repo_root ----

def test_package_repo_root_finds_dotgit():
    """The cc-buddy-bridge repo itself has a .git dir, so the function should
    locate it from within the package."""
    root = update.package_repo_root()
    assert root is not None
    assert (root / ".git").exists()
    # And the repo is the same one we're running from.
    assert (root / "pyproject.toml").exists()


# ---- _git_status_clean (parses subprocess output) ----

def test_git_status_clean_when_no_output(tmp_path, monkeypatch):
    def fake_run(cmd, **_):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()
    monkeypatch.setattr(update.subprocess, "run", fake_run)
    assert update._git_status_clean(tmp_path) is True


def test_git_status_clean_false_when_dirty(tmp_path, monkeypatch):
    def fake_run(cmd, **_):
        class R:
            returncode = 0
            stdout = " M src/foo.py\n"
            stderr = ""
        return R()
    monkeypatch.setattr(update.subprocess, "run", fake_run)
    assert update._git_status_clean(tmp_path) is False


def test_git_status_clean_false_on_git_failure(tmp_path, monkeypatch):
    def fake_run(cmd, **_):
        class R:
            returncode = 128
            stdout = ""
            stderr = "not a git repo"
        return R()
    monkeypatch.setattr(update.subprocess, "run", fake_run)
    assert update._git_status_clean(tmp_path) is False


# ---- run_update early exits ----

def test_run_update_already_on_latest(monkeypatch, capsys):
    monkeypatch.setattr(update, "check", lambda force: _fake_info("0.1.0", "0.1.0"))
    rc = update.run_update(yes=True)
    assert rc == 0
    captured = capsys.readouterr()
    assert "latest" in captured.out.lower()


def test_run_update_network_failure(monkeypatch, capsys):
    monkeypatch.setattr(update, "check", lambda force: _fake_info("0.1.0", None))
    rc = update.run_update(yes=True)
    assert rc == 2
    captured = capsys.readouterr()
    assert "github" in captured.out.lower()


def test_run_update_not_a_git_checkout(monkeypatch, capsys):
    monkeypatch.setattr(update, "check", lambda force: _fake_info("0.1.0", "0.2.0"))
    monkeypatch.setattr(update, "package_repo_root", lambda: None)
    rc = update.run_update(yes=True)
    assert rc == 2
    captured = capsys.readouterr()
    assert "git checkout" in captured.err
    assert "pip install" in captured.err


def test_run_update_refuses_dirty_tree(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(update, "check", lambda force: _fake_info("0.1.0", "0.2.0"))
    monkeypatch.setattr(update, "package_repo_root", lambda: tmp_path)
    monkeypatch.setattr(update, "_git_status_clean", lambda repo: False)
    rc = update.run_update(yes=True)
    assert rc == 2
    captured = capsys.readouterr()
    assert "uncommitted" in captured.err.lower()


def test_run_update_no_tty_no_yes_bails(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(update, "check", lambda force: _fake_info("0.1.0", "0.2.0"))
    monkeypatch.setattr(update, "package_repo_root", lambda: tmp_path)
    monkeypatch.setattr(update, "_git_status_clean", lambda repo: True)
    monkeypatch.setattr(update, "_current_branch", lambda repo: "main")
    monkeypatch.setattr(update, "_detect_service_backend", lambda: "launchd")
    monkeypatch.setattr(update.sys.stdin, "isatty", lambda: False)
    rc = update.run_update(yes=False)
    assert rc == 2
    captured = capsys.readouterr()
    assert "-y" in captured.err


def test_run_update_user_declines(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(update, "check", lambda force: _fake_info("0.1.0", "0.2.0"))
    monkeypatch.setattr(update, "package_repo_root", lambda: tmp_path)
    monkeypatch.setattr(update, "_git_status_clean", lambda repo: True)
    monkeypatch.setattr(update, "_current_branch", lambda repo: "main")
    monkeypatch.setattr(update, "_detect_service_backend", lambda: None)
    monkeypatch.setattr(update.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    rc = update.run_update(yes=False)
    assert rc == 1
    captured = capsys.readouterr()
    assert "Aborted" in captured.out


def test_run_update_full_flow_with_yes(monkeypatch, capsys, tmp_path):
    """End-to-end with -y, all subprocess calls stubbed to success."""
    monkeypatch.setattr(update, "check", lambda force: _fake_info("0.1.0", "0.2.0"))
    monkeypatch.setattr(update, "package_repo_root", lambda: tmp_path)
    monkeypatch.setattr(update, "_git_status_clean", lambda repo: True)
    monkeypatch.setattr(update, "_current_branch", lambda repo: "main")
    monkeypatch.setattr(update, "_detect_service_backend", lambda: "launchd")
    monkeypatch.setattr(update, "_run", lambda cmd, cwd=None: (0, "", ""))
    monkeypatch.setattr(update, "_restart_service",
                        lambda backend: (True, "Daemon restarted."))
    rc = update.run_update(yes=True)
    assert rc == 0
    captured = capsys.readouterr()
    assert "git pull" in captured.out
    assert "pip install" in captured.out
    assert "Daemon restarted" in captured.out


def test_run_update_git_pull_fails(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(update, "check", lambda force: _fake_info("0.1.0", "0.2.0"))
    monkeypatch.setattr(update, "package_repo_root", lambda: tmp_path)
    monkeypatch.setattr(update, "_git_status_clean", lambda repo: True)
    monkeypatch.setattr(update, "_current_branch", lambda repo: "main")
    monkeypatch.setattr(update, "_detect_service_backend", lambda: None)

    calls = []

    def fake_run(cmd, cwd=None):
        calls.append(cmd)
        if cmd[:2] == ["git", "pull"]:
            return 1, "", "fatal: unable to access"
        return 0, "", ""

    monkeypatch.setattr(update, "_run", fake_run)
    rc = update.run_update(yes=True)
    assert rc == 2
    # pip install should NOT have been attempted after git pull failed.
    assert all("pip" not in " ".join(c) for c in calls)
