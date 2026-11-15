from __future__ import annotations

from typing import Final

KNOWN_ARTIFACT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "plan",
        "planning_analysis_decision",
        "development_analysis_decision",
        "review_analysis_decision",
        "policy_remediation_analysis_decision",
        "development_result",
        "product_spec",
        "issues",
        "fix_result",
        "design_verdict",
        "smoke_test_result",
        "run_time_report",
        "commit_review",
        "commit_message",
    }
)

__all__ = ["KNOWN_ARTIFACT_TYPES"]
