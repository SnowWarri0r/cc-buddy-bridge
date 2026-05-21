"""GitHub-Release-based version check for the bridge.

One HTTP call per day to ``https://api.github.com/repos/.../releases/latest``,
cached to disk. The daemon polls every 24 h; the ``check-update`` CLI command
forces a refresh. ``CC_BUDDY_BRIDGE_NO_UPDATE_CHECK=1`` disables both paths.

Firmware version detection is intentionally out of scope here — the stick's
status ack doesn't carry a fw version field, and the upstream firmware repo
has no releases/tags to compare against. Would require an upstream PR.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com/repos/SnowWarri0r/cc-buddy-bridge/releases/latest"
CACHE_TTL_SECS = 24 * 3600
USER_AGENT = f"cc-buddy-bridge/{__version__}"
HTTP_TIMEOUT_SECS = 3.0


def _cache_path() -> Path:
    """Per-platform cache file for the last GitHub query result."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        base = Path.home() / "AppData" / "Local"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "cc-buddy-bridge" / "update_check.json"


def _disabled() -> bool:
    return os.environ.get("CC_BUDDY_BRIDGE_NO_UPDATE_CHECK") == "1"


@dataclass(frozen=True)
class UpdateInfo:
    """One snapshot of the GitHub releases/latest endpoint."""

    current: str
    latest: Optional[str]  # None on first-fetch failure
    fetched_at: float       # unix seconds

    @property
    def has_update(self) -> bool:
        if not self.latest:
            return False
        return _is_newer(self.latest, self.current)

    @property
    def is_fresh(self) -> bool:
        return (time.time() - self.fetched_at) < CACHE_TTL_SECS


# Semver-ish comparison: split on dots and non-digits, compare numerically when
# both parts parse as ints else lexically. Treats "0.1.0" < "0.1.1" < "0.2.0".
# Pre-release suffixes (-rc1, -alpha) compare lexically and are considered older
# than the bare version (e.g. "0.2.0" > "0.2.0-rc1") via the empty-after-dash
# trick. Good enough for our v-prefixed Git tags.
_VPREFIX = re.compile(r"^v")


def _parts(v: str) -> tuple:
    v = _VPREFIX.sub("", (v or "").strip())
    # Split version on '-' so pre-release suffixes compare separately.
    base, _, pre = v.partition("-")
    main = tuple(int(p) if p.isdigit() else p for p in base.split("."))
    # Empty pre sorts AFTER any non-empty pre, so "0.2.0" > "0.2.0-rc1".
    # We encode that as (0, "") for releases and (1, pre) for pre-releases — wait,
    # (False, "") < (False, "rc1") lexically. We want bare-version > pre-release.
    # Trick: invert the boolean — release = (True,), pre-release = (False, pre).
    pre_tuple = (True, "") if not pre else (False, pre)
    return main + (pre_tuple,)


def _is_newer(candidate: str, baseline: str) -> bool:
    """True iff candidate > baseline. Robust against missing/garbage strings."""
    if not candidate or not baseline:
        return False
    try:
        return _parts(candidate) > _parts(baseline)
    except (TypeError, ValueError):
        return False


def _fetch_latest() -> Optional[str]:
    """Network call. Returns the tag_name of the latest release, or None on failure."""
    req = urllib.request.Request(GITHUB_API, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECS) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        log.debug("update check: GitHub fetch failed: %s", e)
        return None
    tag = payload.get("tag_name")
    if not isinstance(tag, str):
        return None
    return tag


def _load_cache() -> Optional[UpdateInfo]:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return UpdateInfo(
            current=str(data["current"]),
            latest=data.get("latest"),
            fetched_at=float(data["fetched_at"]),
        )
    except (KeyError, ValueError, TypeError):
        return None


def _save_cache(info: UpdateInfo) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump({
                "current": info.current,
                "latest": info.latest,
                "fetched_at": info.fetched_at,
            }, f)
    except OSError as e:
        log.debug("update check: cache write failed: %s", e)


def check(*, force: bool = False) -> UpdateInfo:
    """Return current update state.

    With ``force=False`` (the default), reuse a fresh cache if available.
    With ``force=True``, always hit the network. Returns a stale-cache or
    current-only UpdateInfo if disabled or the network call fails.
    """
    current = __version__
    if _disabled():
        return UpdateInfo(current=current, latest=None, fetched_at=time.time())

    if not force:
        cached = _load_cache()
        if cached is not None and cached.is_fresh and cached.current == current:
            return cached

    latest = _fetch_latest()
    info = UpdateInfo(current=current, latest=latest, fetched_at=time.time())
    if latest is not None:
        _save_cache(info)
    return info


# ---- CLI helper ----

def render_cli(info: UpdateInfo) -> str:
    """Human-friendly multi-line summary for ``cc-buddy-bridge check-update``."""
    lines = [f"Installed:   {info.current}"]
    if info.latest is None:
        lines.append("Latest:      (could not reach github.com — try again later)")
        return "\n".join(lines)
    lines.append(f"Latest:      {info.latest}")
    if info.has_update:
        lines.append("")
        lines.append(f"Update available: {info.current} → {info.latest}")
        lines.append("Pull with:        git pull && pip install -e .")
        lines.append("Then restart:     cc-buddy-bridge install --service  (or kickstart the daemon)")
    else:
        lines.append("You're on the latest release.")
    return "\n".join(lines)
