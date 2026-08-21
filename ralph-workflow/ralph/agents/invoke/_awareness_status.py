"""The two shapes a workspace monitor reports its own freshness in.

Split out of :mod:`ralph.agents.invoke._workspace`: what a lease says
about itself is a small vocabulary of its own, and every caller of it
reads the same five keys.
"""

from __future__ import annotations

__all__ = ["current_status", "live_fallback_status"]


def current_status() -> dict[str, object]:
    """Return the status for an active event observer."""
    return {
        "mode": "watch",
        "freshness": "current",
        "cause": None,
        "automatic_recovery": False,
        "safe_next_action": "None required.",
    }


def live_fallback_status(cause: str) -> dict[str, object]:
    """Return the bounded, explicit status used without a host observer."""
    return {
        "mode": "live_fallback",
        "freshness": "live_fallback",
        "cause": cause,
        "automatic_recovery": True,
        "safe_next_action": "Ralph will retry observation on the next workspace lease.",
    }
