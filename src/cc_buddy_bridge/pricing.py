"""Per-message cost estimation for Claude API usage.

Pure-function module: takes a ``model`` id + ``usage`` dict (as it appears in
~/.claude/projects/*.jsonl assistant records) and returns a USD estimate.

Rates as of 2026-05, USD per million tokens. Two cache-write tiers exist
because the 1-hour extended cache costs ~2× the 5-minute default cache.
Cache reads are ~10% of the input rate. Override rates by editing this
file or fork — there's intentionally no separate config layer to manage.

This is an *estimate*, not a billing source of truth. We don't account for
service_tier discounts, batch pricing, or per-request volume tiers. Good
enough for a heads-up "$N.NN today" in the statusline.
"""

from __future__ import annotations

# input / output / cache_write_5m / cache_write_1h / cache_read
_RATES: dict[str, dict[str, float]] = {
    "opus":   {"input": 15.0, "output": 75.0, "cache_write_5m": 18.75, "cache_write_1h": 30.0, "cache_read": 1.50},
    "sonnet": {"input":  3.0, "output": 15.0, "cache_write_5m":  3.75, "cache_write_1h":  6.0, "cache_read": 0.30},
    "haiku":  {"input":  1.0, "output":  5.0, "cache_write_5m":  1.25, "cache_write_1h":  2.0, "cache_read": 0.10},
}

# Unknown models bill at Sonnet rates — the middle of the road. We log the
# unknown model id once per process at daemon startup so a missing entry is
# surfaced rather than silently mispriced; see jsonl_tailer.py.
_DEFAULT_FAMILY = "sonnet"


def family_of(model_id: str) -> str:
    """Resolve a Claude model id ('claude-opus-4-7', 'claude-haiku-4-5-...') to a rate family."""
    if not model_id:
        return _DEFAULT_FAMILY
    m = model_id.lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    if "sonnet" in m:
        return "sonnet"
    return _DEFAULT_FAMILY


def estimate_cost(model_id: str, usage: dict) -> float:
    """USD cost for a single message's usage object.

    Prefers the per-TTL cache breakdown (``usage.cache_creation.ephemeral_*_input_tokens``)
    because 1-hour cache writes cost 2× the 5-minute rate. Falls back to the
    flat ``cache_creation_input_tokens`` total at the 5-minute rate when the
    breakdown isn't present (older transcripts).
    """
    family = family_of(model_id)
    r = _RATES.get(family, _RATES[_DEFAULT_FAMILY])

    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)

    cache_breakdown = usage.get("cache_creation") or {}
    cache_write_5m = int(cache_breakdown.get("ephemeral_5m_input_tokens") or 0)
    cache_write_1h = int(cache_breakdown.get("ephemeral_1h_input_tokens") or 0)
    if not cache_write_5m and not cache_write_1h:
        cache_write_5m = int(usage.get("cache_creation_input_tokens") or 0)

    total = (
        inp * r["input"]
        + out * r["output"]
        + cache_read * r["cache_read"]
        + cache_write_5m * r["cache_write_5m"]
        + cache_write_1h * r["cache_write_1h"]
    )
    return total / 1_000_000.0
