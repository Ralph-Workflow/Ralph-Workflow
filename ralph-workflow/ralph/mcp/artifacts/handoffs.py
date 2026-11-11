
from __future__ import annotations

HANDOFF_PATHS: dict[str, str] = {
    "plan": ".agent/PLAN.md",
    "issues": ".agent/ISSUES.md",
    "development_result": ".agent/DEVELOPMENT_RESULT.md",
    "parallel_development_summary": ".agent/DEVELOPMENT_RESULT.md",
    "fix_result": ".agent/FIX_RESULT.md",
    "development_analysis_decision": ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
    "planning_analysis_decision": ".agent/PLANNING_ANALYSIS_DECISION.md",
    "review_analysis_decision": ".agent/REVIEW_ANALYSIS_DECISION.md",
}


def handoff_path_for_artifact(artifact_type: str) -> str | None:
    return HANDOFF_PATHS.get(artifact_type)


__all__ = [
    "HANDOFF_PATHS",
    "handoff_path_for_artifact",
]
