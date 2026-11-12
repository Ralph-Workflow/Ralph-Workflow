from __future__ import annotations

__all__ = ["current_status", "live_fallback_status"]


def current_status() -> dict[str, object]:
    return {
        "mode": "watch",
        "freshness": "current",
        "cause": None,
        "automatic_recovery": False,
        "safe_next_action": "None required.",
    }


def live_fallback_status(cause: str) -> dict[str, object]:
    return {
        "mode": "live_fallback",
        "freshness": "live_fallback",
        "cause": cause,
        "automatic_recovery": True,
        "safe_next_action": "Ralph will retry observation on the next workspace lease.",
    }
