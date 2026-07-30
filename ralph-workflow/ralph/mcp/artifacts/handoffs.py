"""Agent/user-facing Markdown handoff path mapping.

Markdown artifacts are Ralph's source of truth. Submission
(:mod:`ralph.mcp.artifacts.canonical_submit`) writes the validated markdown
document to ``.agent/artifacts/<type>.md`` and byte-copies it to the handoff
path declared here, so downstream agents and users read the submitted document
directly — there is no derivation or rendering step.
"""

from __future__ import annotations

HANDOFF_PATHS: dict[str, str] = {
    "plan": ".agent/PLAN.md",
    "issues": ".agent/ISSUES.md",
    "development_result": ".agent/DEVELOPMENT_RESULT.md",
    # parallel_development_summary reuses DEVELOPMENT_RESULT.md so the analysis
    # phase picks it up through the same fallback path without code changes.
    "parallel_development_summary": ".agent/DEVELOPMENT_RESULT.md",
    "fix_result": ".agent/FIX_RESULT.md",
    "development_analysis_decision": ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
    "planning_analysis_decision": ".agent/PLANNING_ANALYSIS_DECISION.md",
    "review_analysis_decision": ".agent/REVIEW_ANALYSIS_DECISION.md",
}


def handoff_path_for_artifact(artifact_type: str) -> str | None:
    """Return the Markdown handoff path for an artifact type, if any."""
    return HANDOFF_PATHS.get(artifact_type)


__all__ = [
    "HANDOFF_PATHS",
    "handoff_path_for_artifact",
]
