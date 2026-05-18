"""Tests for artifact submit/rollback symmetry via submit_ops_for_artifact."""

from __future__ import annotations

import json

import pytest

from ralph.mcp.artifacts.typed_artifacts import (
    TypedArtifactValidationError,
    normalize_analysis_decision_content,
)

_COMMIT_MESSAGE_OP_COUNT = 3
_PLAN_OP_COUNT = 3
_GENERIC_OP_COUNT = 2
_EXPECTED_IMMEDIATE_HISTORY_SNAPSHOTS = 2

# Minimal valid content for each artifact type that passes validation
_VALID_CONTENT: dict[str, str] = {
    "plan": json.dumps(
        {
            "summary": {
                "context": "Test plan submission",
                "scope_items": [
                    {"text": "Implement feature"},
                    {"text": "Write tests"},
                    {"text": "Verify"},
                ],
            },
            "steps": [{"number": 1, "title": "Step 1", "content": "Do the work"}],
            "critical_files": {
                "primary_files": [{"path": "ralph/pipeline/runner.py", "action": "modify"}]
            },
            "risks_mitigations": [{"risk": "Regression", "mitigation": "Tests"}],
            "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
        }
    ),
    "development_result": json.dumps(
        {"status": "completed", "summary": "Feature implemented", "files_changed": "- src/a.py"}
    ),
    "issues": json.dumps(
        {
            "status": "no_issues",
            "summary": "No issues found",
            "issues": [],
            "what_came_up_short": [],
            "how_to_fix": [],
        }
    ),
    "fix_result": json.dumps({"summary": "Fixed the regression", "files_changed": "- src/b.py"}),
    "commit_message": json.dumps({"type": "commit", "subject": "feat: add per-type tests"}),
    "development_analysis_decision": json.dumps(
        {"status": "completed", "summary": "Analysis complete"}
    ),
    "review_analysis_decision": json.dumps({"status": "completed", "summary": "Review approved"}),
}

# Invalid content (wrong type / missing required field) for each artifact type
_INVALID_CONTENT: dict[str, str] = {
    "plan": json.dumps({"summary": "missing steps field"}),
    "development_result": json.dumps({"status": "bad_status"}),
    "issues": json.dumps({"status": "bad_status"}),
    "fix_result": json.dumps({}),  # missing required summary
    "commit_message": json.dumps({"subject": "no-conventional-prefix"}),
    "development_analysis_decision": json.dumps({"status": "unknown_status"}),
    "review_analysis_decision": json.dumps({"status": "unknown_status"}),
}

_ALL_ARTIFACT_TYPES = list(_VALID_CONTENT.keys())


class TestAnalysisDecisionValidationContract:
    """Tests that AnalysisDecision enforces the documented artifact contract."""

    def test_completed_without_remediation_is_valid(self) -> None:
        result = normalize_analysis_decision_content({"status": "completed", "summary": "All good"})
        assert result["status"] == "completed"

    def test_request_changes_requires_what_came_up_short(self) -> None:
        with pytest.raises(TypedArtifactValidationError, match="what_came_up_short"):
            normalize_analysis_decision_content(
                {
                    "status": "request_changes",
                    "summary": "Needs work",
                    "how_to_fix": ["Fix it"],
                }
            )

    def test_request_changes_requires_how_to_fix(self) -> None:
        with pytest.raises(TypedArtifactValidationError, match="how_to_fix"):
            normalize_analysis_decision_content(
                {
                    "status": "request_changes",
                    "summary": "Needs work",
                    "what_came_up_short": ["Missing tests"],
                }
            )

    def test_failed_requires_remediation_fields(self) -> None:
        with pytest.raises(TypedArtifactValidationError):
            normalize_analysis_decision_content(
                {"status": "failed", "summary": "Analysis could not complete"}
            )

    def test_request_changes_with_all_fields_is_valid(self) -> None:
        result = normalize_analysis_decision_content(
            {
                "status": "request_changes",
                "summary": "Needs work",
                "what_came_up_short": ["Missing tests"],
                "how_to_fix": ["Add tests for edge cases"],
            }
        )
        assert result["status"] == "request_changes"

    def test_unknown_status_rejected(self) -> None:
        with pytest.raises(TypedArtifactValidationError):
            normalize_analysis_decision_content({"status": "unknown_status", "summary": "test"})

    def test_legacy_synonyms_rejected_at_submission(self) -> None:
        for status in ("loopback", "retry", "approve", "approved", "reject"):
            with pytest.raises(TypedArtifactValidationError, match="status"):
                normalize_analysis_decision_content({"status": status, "summary": "test"})
