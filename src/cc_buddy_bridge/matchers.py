"""Classify Bash commands into allow / ask / default tiers.

Rules live in two places:

1. Baked-in defaults at module scope — sensible starting point so a fresh
   install works without touching any config file.
2. Optional override at ``$XDG_CONFIG_HOME/cc-buddy-bridge/matchers.toml``
   (defaults to ``~/.config/cc-buddy-bridge/matchers.toml``). The file can
   override or extend either list.

TOML shape::

    # Commands matching an auto_allow pattern are approved without bothering
    # the stick. Use for frequent, non-destructive reads.
    auto_allow = [
        "^ls( |$)",
        "^cat( |$)",
    ]

    # Commands matching an always_ask pattern always go through the stick's
    # button round-trip, even if the user's default permission mode is "auto".
    always_ask = [
        "^rm( |$)",
        "^sudo( |$)",
    ]

    # If true, the built-in defaults are discarded and your lists are the only
    # rules. Default false (your lists extend the built-ins).
    replace_defaults = false

Classification precedence: **always_ask beats auto_allow** when both match
(the author of a config who explicitly asks for confirmation on `rm` shouldn't
be silently overridden by a loose `auto_allow` pattern like `^r.+`).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

Decision = Literal["allow", "ask", "default"]


# --- baked-in defaults ---------------------------------------------------

# Read-only / universally safe commands. Users don't need a physical button
# press to run `ls` every few seconds.
DEFAULT_AUTO_ALLOW: tuple[str, ...] = (
    r"^ls( |$)",
    r"^ll( |$)",
    r"^cat( |$)",
    r"^head( |$)",
    r"^tail( |$)",
    r"^less( |$)",
    r"^more( |$)",
    r"^echo( |$)",
    r"^printf( |$)",
    r"^pwd$",
    r"^whoami$",
    r"^id$",
    r"^uname( |$)",
    r"^date( |$)",
    r"^which( |$)",
    r"^type( |$)",
    r"^file( |$)",
    r"^stat( |$)",
    r"^du( |$)",
    r"^df( |$)",
    r"^wc( |$)",
    r"^grep( |$)",
    r"^rg( |$)",
    r"^ag( |$)",
    r"^fd( |$)",
    r"^find( |$)",  # we ask separately for `find ... -delete`; see always_ask
    r"^tree( |$)",
    r"^env( |$)",
    r"^ps( |$)",
    r"^top( |$)",
    r"^free( |$)",
    r"^history( |$)",
    r"^git status( |$)",
    r"^git diff( |$)",
    r"^git log( |$)",
    r"^git show( |$)",
    r"^git branch( |$)",
    r"^git blame( |$)",
    r"^git stash list( |$)",
    r"^git remote -v",
    r"^git config --get ",
    r"^node --version",
    r"^npm --version",
    r"^python --version",
    r"^python3 --version",
    r"^pytest(\s+-[vq])?\s*$",  # bare `pytest -q`
)

# Destructive, networked, privileged, or history-rewriting commands. Always
# push to the stick even if the user's default permission mode is "auto".
DEFAULT_ALWAYS_ASK: tuple[str, ...] = (
    r"^sudo( |$)",
    r"^su( |$)",
    r"^rm( |$)",
    r"^rmdir( |$)",
    r"^dd( |$)",
    r"^shred( |$)",
    r"^mv ",        # mv can clobber — surface it
    r"^chmod( |$)",
    r"^chown( |$)",
    r"^curl( |$)",
    r"^wget( |$)",
    r"^nc ",
    r"^ssh( |$)",
    r"^scp( |$)",
    r"^rsync( |$)",
    r"^ftp( |$)",
    r"^git push( |$)",
    r"^git reset --hard",
    r"^git clean( |$)",
    r"^git rebase( |$)",
    r"^git filter-(branch|repo)",
    r"^git checkout -- ",
    r"^git restore( |$)",
    r"^git branch -[dD]( |$)",
    r"^git tag -d( |$)",
    r"^docker( |$)",
    r"^podman( |$)",
    r"^kubectl( |$)",
    r"^helm( |$)",
    r"^terraform( |$)",
    r"^aws( |$)",
    r"^gcloud( |$)",
    r"^npm (install|publish|uninstall)( |$)",
    r"^yarn (add|remove|publish)( |$)",
    r"^pnpm (install|add|remove|publish)( |$)",
    r"^pip install",
    r"^pip3 install",
    r"^pipx install",
    r"^brew (install|uninstall|upgrade)( |$)",
    r"^apt(-get)? (install|remove|purge|upgrade)( |$)",
    r"^systemctl( |$)",
    r"^launchctl( |$)",
    r"^kill( |$)",
    r"^killall( |$)",
    r"^pkill( |$)",
    r"^mkfs\b",
    r"^mount( |$)",
    r"^umount( |$)",
    r"^find\b.*-delete\b",
    r"^find\b.*-exec\b",
    r"^xargs\b.*\b(rm|mv|chmod|chown)\b",
    r"^>\s*/dev/",      # redirection to /dev/*
    r"^gh (pr|issue|repo) (create|merge|delete|edit)",
)


@dataclass(frozen=True)
class MatcherConfig:
    auto_allow: tuple[re.Pattern[str], ...] = field(default_factory=tuple)
    always_ask: tuple[re.Pattern[str], ...] = field(default_factory=tuple)


def _config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "cc-buddy-bridge" / "matchers.toml"
    return Path.home() / ".config" / "cc-buddy-bridge" / "matchers.toml"


def _compile(patterns) -> tuple[re.Pattern[str], ...]:  # type: ignore[no-untyped-def]
    out: list[re.Pattern[str]] = []
    for p in patterns:
        try:
            out.append(re.compile(p))
        except re.error as e:
            log.warning("matchers: ignoring bad regex %r (%s)", p, e)
    return tuple(out)


def load_config(path: Path | None = None) -> MatcherConfig:
    """Load rules. Falls back to built-in defaults if the file is missing or bad.

    Tomllib ships in 3.11+, so no new dependency."""
    target = path if path is not None else _config_path()

    auto_allow_patterns: list[str] = list(DEFAULT_AUTO_ALLOW)
    always_ask_patterns: list[str] = list(DEFAULT_ALWAYS_ASK)

    if target.exists():
        try:
            import tomllib  # stdlib (Python 3.11+)
            with target.open("rb") as f:
                data = tomllib.load(f)
        except Exception as e:  # noqa: BLE001
            log.warning("matchers: failed to parse %s (%s); using defaults", target, e)
            return MatcherConfig(auto_allow=_compile(auto_allow_patterns), always_ask=_compile(always_ask_patterns))

        user_auto_allow = data.get("auto_allow") or []
        user_always_ask = data.get("always_ask") or []
        replace = bool(data.get("replace_defaults", False))

        if replace:
            auto_allow_patterns = list(user_auto_allow)
            always_ask_patterns = list(user_always_ask)
        else:
            auto_allow_patterns = list(DEFAULT_AUTO_ALLOW) + list(user_auto_allow)
            always_ask_patterns = list(DEFAULT_ALWAYS_ASK) + list(user_always_ask)

    return MatcherConfig(
        auto_allow=_compile(auto_allow_patterns),
        always_ask=_compile(always_ask_patterns),
    )


def classify_command(command: str, cfg: MatcherConfig) -> Decision:
    """Returns "allow" | "ask" | "default".

    always_ask beats auto_allow when both match, so a loose allow pattern can't
    accidentally silence a deliberate ask pattern.
    """
    if not command:
        return "default"
    for pat in cfg.always_ask:
        if pat.search(command):
            return "ask"
    for pat in cfg.auto_allow:
        if pat.search(command):
            return "allow"
    return "default"
