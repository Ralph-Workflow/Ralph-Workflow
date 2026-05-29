"""Tests for artifact submit/rollback symmetry via submit_ops_for_artifact."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.artifact import (
    handle_submit_artifact,
)
from ralph.mcp.tools.coordination import InvalidParamsError

if TYPE_CHECKING:
    from pathlib import Path
from tests.test_mcp_tool_artifact_rollback_invalid_content_rollback_helper__session import _Session
from tests.test_mcp_tool_artifact_rollback_invalid_content_rollback_helper__workspace import (
    _Workspace,
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
            "skills_mcp": {
                "skills": [
                    "test-driven-development",
                    "verification-before-completion",
                ],
                "mcps": [],
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






class TestInvalidContentRollback:
    """Invalid content triggers validation error with no artifact left on disk."""

    @pytest.mark.parametrize("artifact_type", list(_INVALID_CONTENT.keys()))
    def test_invalid_submit_raises_and_leaves_no_artifact(
        self, artifact_type: str, tmp_path: Path
    ) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _Session()

        with pytest.raises(InvalidParamsError):
            handle_submit_artifact(
                session,
                workspace,
                {
                    "artifact_type": artifact_type,
                    "content": _INVALID_CONTENT[artifact_type],
                },
            )

        artifact_file = artifact_dir / f"{artifact_type}.json"
        assert not artifact_file.exists(), (
            f"{artifact_type}: no artifact must remain on disk after validation failure"
        )

