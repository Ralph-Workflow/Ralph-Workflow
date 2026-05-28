"""Tests for artifact submit/rollback symmetry via submit_ops_for_artifact."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.tools.artifact import (
    handle_submit_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path
from tests.test_mcp_tool_artifact_rollback_rollback_symmetry_helper__session import _Session
from tests.test_mcp_tool_artifact_rollback_rollback_symmetry_helper__workspace import _Workspace

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


class TestRollbackSymmetry:
    """Integration tests: on exception after JSON submit, markdown is also rolled back."""

    def test_rollback_removes_json_artifact_and_markdown_on_post_submit_failure(
        self, tmp_path: Path
    ) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _Session()

        # Patch sync_markdown_handoff to raise after JSON is written
        with (
            patch(
                "ralph.mcp.tools.artifact.sync_markdown_handoff",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(OSError, match="disk full"),
        ):
            handle_submit_artifact(
                session,
                workspace,
                {
                    "artifact_type": "development_analysis_decision",
                    "content": '{"status":"completed","summary":"done"}',
                },
            )

        # JSON artifact should be rolled back
        artifact_file = artifact_dir / "development_analysis_decision.json"
        assert not artifact_file.exists()

    def test_successful_submit_leaves_both_json_and_markdown(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _Session()

        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "development_analysis_decision",
                "content": '{"status":"completed","summary":"done"}',
            },
        )

        artifact_file = artifact_dir / "development_analysis_decision.json"
        assert artifact_file.exists()

    def test_commit_message_rollback_cleans_up_all_artifacts_on_markdown_failure(
        self, tmp_path: Path
    ) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _Session()

        with (
            patch(
                "ralph.mcp.tools.artifact.sync_markdown_handoff",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(OSError),
        ):
            handle_submit_artifact(
                session,
                workspace,
                {
                    "artifact_type": "commit_message",
                    "content": '{"type":"commit","subject":"feat: add rollback test"}',
                },
            )

        artifact_file = artifact_dir / "commit_message.json"
        assert not artifact_file.exists()
