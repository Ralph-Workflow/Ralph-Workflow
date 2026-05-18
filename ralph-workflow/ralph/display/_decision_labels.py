"""Decision-to-badge label mapping for pipeline completion summaries."""

from __future__ import annotations

DECISION_BADGE_MAP: dict[str, str] = {
    "proceed": "PASS",
    "complete": "PASS",
    "pr_opened": "INFO",
    "revise": "WARN",
    "failed": "FAIL",
}

PROCEED_DECISION_VALUES: frozenset[str] = frozenset({"proceed", "complete", "pr_opened"})
REVISE_DECISION_VALUES: frozenset[str] = frozenset({"revise", "failed"})

__all__ = ["DECISION_BADGE_MAP", "PROCEED_DECISION_VALUES", "REVISE_DECISION_VALUES"]
