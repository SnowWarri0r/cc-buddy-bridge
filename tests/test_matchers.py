"""Tests for matchers.py — classification logic + TOML loading."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cc_buddy_bridge.matchers import (
    MatcherConfig,
    classify_command,
    load_config,
)


# ---- classify_command against baked-in defaults ----

@pytest.fixture(scope="module")
def defaults() -> MatcherConfig:
    return load_config(path=Path("/nonexistent.toml"))


def test_ls_is_auto_allowed(defaults: MatcherConfig):
    assert classify_command("ls -la /tmp", defaults) == "allow"


def test_cat_is_auto_allowed(defaults: MatcherConfig):
    assert classify_command("cat README.md", defaults) == "allow"


def test_git_status_is_auto_allowed(defaults: MatcherConfig):
    assert classify_command("git status", defaults) == "allow"


def test_rm_is_always_ask(defaults: MatcherConfig):
    assert classify_command("rm -rf /tmp/foo", defaults) == "ask"


def test_sudo_is_always_ask(defaults: MatcherConfig):
    assert classify_command("sudo apt upgrade", defaults) == "ask"


def test_curl_is_always_ask(defaults: MatcherConfig):
    assert classify_command("curl https://example.com", defaults) == "ask"


def test_git_push_is_always_ask(defaults: MatcherConfig):
    assert classify_command("git push origin main", defaults) == "ask"


def test_pip_install_is_always_ask(defaults: MatcherConfig):
    assert classify_command("pip install requests", defaults) == "ask"


def test_find_delete_is_always_ask(defaults: MatcherConfig):
    assert classify_command("find . -name '*.pyc' -delete", defaults) == "ask"


def test_unknown_command_is_default(defaults: MatcherConfig):
    assert classify_command("some-custom-script --flag", defaults) == "default"


def test_empty_command_is_default(defaults: MatcherConfig):
    assert classify_command("", defaults) == "default"


def test_always_ask_beats_auto_allow():
    cfg = MatcherConfig(
        auto_allow=tuple(re.compile(p) for p in [r"^ls( |$)", r"^rm( |$)"]),
        always_ask=tuple(re.compile(p) for p in [r"^rm( |$)"]),
    )
    assert classify_command("rm -rf x", cfg) == "ask"


# ---- TOML loading ----

def test_load_config_no_file_returns_defaults(tmp_path: Path):
    cfg = load_config(path=tmp_path / "nope.toml")
    assert classify_command("ls", cfg) == "allow"
    assert classify_command("rm file", cfg) == "ask"


def test_load_config_extends_defaults(tmp_path: Path):
    cfg_path = tmp_path / "matchers.toml"
    cfg_path.write_text(
        'auto_allow = ["^myapp( |$)"]\n'
        'always_ask = ["^migrate( |$)"]\n',
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_path)
    assert classify_command("ls", cfg) == "allow"
    assert classify_command("rm x", cfg) == "ask"
    assert classify_command("myapp start", cfg) == "allow"
    assert classify_command("migrate down", cfg) == "ask"


def test_load_config_replace_defaults(tmp_path: Path):
    cfg_path = tmp_path / "matchers.toml"
    cfg_path.write_text(
        'replace_defaults = true\n'
        'auto_allow = ["^myapp( |$)"]\n',
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_path)
    assert classify_command("ls", cfg) == "default"
    assert classify_command("myapp x", cfg) == "allow"
    assert classify_command("rm x", cfg) == "default"


def test_load_config_bad_regex_is_skipped(tmp_path: Path):
    cfg_path = tmp_path / "matchers.toml"
    cfg_path.write_text(
        'auto_allow = ["[unclosed", "^ok( |$)"]\n',
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_path)
    assert classify_command("ok", cfg) == "allow"


def test_load_config_bad_toml_falls_back_to_defaults(tmp_path: Path):
    cfg_path = tmp_path / "matchers.toml"
    cfg_path.write_text("this is {not} [valid toml", encoding="utf-8")
    cfg = load_config(path=cfg_path)
    assert classify_command("ls", cfg) == "allow"
