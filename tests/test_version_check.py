"""Tests for the GitHub-Release version check helper."""

from __future__ import annotations

import time

import pytest

from cc_buddy_bridge import version_check as vc

# ---- _is_newer ----

@pytest.mark.parametrize("candidate,baseline,expected", [
    ("0.1.1", "0.1.0", True),
    ("0.2.0", "0.1.99", True),
    ("1.0.0", "0.99.99", True),
    ("0.1.0", "0.1.0", False),
    ("0.1.0", "0.1.1", False),
    ("v0.1.1", "v0.1.0", True),       # tolerates 'v' prefix on either side
    ("0.1.1", "v0.1.0", True),
    ("v0.1.0", "0.1.1", False),
    ("0.2.0", "0.2.0-rc1", True),     # bare release > pre-release
    ("0.2.0-rc2", "0.2.0-rc1", True), # rc2 > rc1 lexically
    ("", "0.1.0", False),             # empty inputs are safe
    ("0.1.0", "", False),
])
def test_is_newer(candidate, baseline, expected):
    assert vc._is_newer(candidate, baseline) is expected


# ---- UpdateInfo / has_update / is_fresh ----

def test_update_info_has_update_with_no_latest():
    info = vc.UpdateInfo(current="0.1.0", latest=None, fetched_at=time.time())
    assert info.has_update is False


def test_update_info_has_update_with_newer_latest():
    info = vc.UpdateInfo(current="0.1.0", latest="0.1.1", fetched_at=time.time())
    assert info.has_update is True


def test_update_info_is_fresh_within_ttl():
    info = vc.UpdateInfo(current="0.1.0", latest="0.1.0", fetched_at=time.time() - 100)
    assert info.is_fresh is True


def test_update_info_is_stale_past_ttl():
    info = vc.UpdateInfo(current="0.1.0", latest="0.1.0",
                         fetched_at=time.time() - vc.CACHE_TTL_SECS - 100)
    assert info.is_fresh is False


# ---- cache load/save ----

def test_cache_roundtrip(tmp_path, monkeypatch):
    cache_file = tmp_path / "update_check.json"
    monkeypatch.setattr(vc, "_cache_path", lambda: cache_file)
    saved = vc.UpdateInfo(current="0.1.0", latest="0.1.2", fetched_at=1700000000.0)
    vc._save_cache(saved)
    loaded = vc._load_cache()
    assert loaded == saved


def test_cache_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(vc, "_cache_path", lambda: tmp_path / "nope.json")
    assert vc._load_cache() is None


def test_cache_corrupt_returns_none(tmp_path, monkeypatch):
    cache_file = tmp_path / "update_check.json"
    cache_file.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(vc, "_cache_path", lambda: cache_file)
    assert vc._load_cache() is None


# ---- check() ----

def test_check_disabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_BUDDY_BRIDGE_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(vc, "_cache_path", lambda: tmp_path / "c.json")
    # Should NOT hit the network even if forced.
    monkeypatch.setattr(vc, "_fetch_latest", lambda: pytest.fail("network was hit"))
    info = vc.check(force=True)
    assert info.latest is None
    assert info.has_update is False


def test_check_uses_fresh_cache(monkeypatch, tmp_path):
    cache_file = tmp_path / "c.json"
    monkeypatch.setattr(vc, "_cache_path", lambda: cache_file)
    # Seed a fresh cache that matches current version.
    fresh = vc.UpdateInfo(current=vc.__version__, latest="0.99.0", fetched_at=time.time())
    vc._save_cache(fresh)
    monkeypatch.setattr(vc, "_fetch_latest", lambda: pytest.fail("network was hit"))
    info = vc.check(force=False)
    assert info.latest == "0.99.0"


def test_check_force_ignores_cache(monkeypatch, tmp_path):
    cache_file = tmp_path / "c.json"
    monkeypatch.setattr(vc, "_cache_path", lambda: cache_file)
    fresh = vc.UpdateInfo(current=vc.__version__, latest="0.99.0", fetched_at=time.time())
    vc._save_cache(fresh)
    monkeypatch.setattr(vc, "_fetch_latest", lambda: "1.2.3")
    info = vc.check(force=True)
    assert info.latest == "1.2.3"


def test_check_network_failure_returns_no_latest(monkeypatch, tmp_path):
    monkeypatch.setattr(vc, "_cache_path", lambda: tmp_path / "c.json")
    monkeypatch.setattr(vc, "_fetch_latest", lambda: None)
    info = vc.check(force=True)
    assert info.latest is None
    assert info.has_update is False


def test_check_stale_cache_triggers_refetch(monkeypatch, tmp_path):
    cache_file = tmp_path / "c.json"
    monkeypatch.setattr(vc, "_cache_path", lambda: cache_file)
    stale = vc.UpdateInfo(current=vc.__version__, latest="0.0.1",
                          fetched_at=time.time() - vc.CACHE_TTL_SECS - 100)
    vc._save_cache(stale)
    monkeypatch.setattr(vc, "_fetch_latest", lambda: "0.5.0")
    info = vc.check(force=False)
    assert info.latest == "0.5.0"


def test_check_cached_version_mismatch_triggers_refetch(monkeypatch, tmp_path):
    """If user upgraded since cache was written, the cache is invalid for them."""
    cache_file = tmp_path / "c.json"
    monkeypatch.setattr(vc, "_cache_path", lambda: cache_file)
    fresh_but_other_install = vc.UpdateInfo(current="0.0.1", latest="0.0.2",
                                            fetched_at=time.time())
    vc._save_cache(fresh_but_other_install)
    monkeypatch.setattr(vc, "_fetch_latest", lambda: "2.0.0")
    info = vc.check(force=False)
    assert info.latest == "2.0.0"


# ---- render_cli ----

def test_render_cli_no_update():
    info = vc.UpdateInfo(current="0.1.0", latest="0.1.0", fetched_at=time.time())
    out = vc.render_cli(info)
    assert "Installed:" in out
    assert "0.1.0" in out
    assert "latest" in out.lower()
    assert "Update available" not in out


def test_render_cli_has_update():
    info = vc.UpdateInfo(current="0.1.0", latest="0.2.0", fetched_at=time.time())
    out = vc.render_cli(info)
    assert "Update available: 0.1.0 → 0.2.0" in out
    assert "git pull" in out


def test_render_cli_no_latest():
    info = vc.UpdateInfo(current="0.1.0", latest=None, fetched_at=time.time())
    out = vc.render_cli(info)
    assert "could not reach github" in out.lower()
