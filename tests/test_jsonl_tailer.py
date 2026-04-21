"""Tests for jsonl_tailer — focused on the parsing helpers rather than filesystem watching."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cc_buddy_bridge.jsonl_tailer import _record_is_today, _today_key


def test_record_is_today_matches_local_day():
    now = datetime.now(tz=timezone.utc)
    ts = now.isoformat().replace("+00:00", "Z")
    assert _record_is_today(ts, _today_key())


def test_record_is_today_rejects_yesterday():
    past = datetime.now(tz=timezone.utc) - timedelta(days=2)
    ts = past.isoformat().replace("+00:00", "Z")
    assert not _record_is_today(ts, _today_key())


def test_record_is_today_rejects_non_strings():
    assert not _record_is_today(None, _today_key())
    assert not _record_is_today(12345, _today_key())
    assert not _record_is_today("", _today_key())


def test_record_is_today_rejects_bad_iso():
    assert not _record_is_today("not-a-date", _today_key())
    assert not _record_is_today("2026/04/22", _today_key())


def test_record_is_today_handles_z_suffix():
    # Mid-day UTC → always the same day regardless of timezone (well, almost).
    # Use a timestamp fresh enough that it's definitely today in any tz.
    ts = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
    assert _record_is_today(ts, _today_key())
