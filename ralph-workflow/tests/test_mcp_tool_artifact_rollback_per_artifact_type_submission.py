"""Tests for artifact submit/rollback symmetry via submit_ops_for_artifact."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.artifacts.handoffs import HANDOFF_PATHS
from ralph.mcp.tools.artifact import (
    handle_submit_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path
from tests.test_mcp_tool_artifact_rollback_per_artifact_type_submission_helper__session import (
    _Session,
)
from tests.test_mcp_tool_artifact_rollback_per_artifact_type_submission_helper__workspace import (
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






class TestPerArtifactTypeSubmission:
    """Parametrized per-artifact-type end-to-end submission tests.

    Covers: plan, development_result, issues, fix_result, commit_message,
    development_analysis_decision, review_analysis_decision.
    """

    @pytest.mark.parametrize("artifact_type", _ALL_ARTIFACT_TYPES)
    def test_valid_submit_produces_json_artifact(self, artifact_type: str, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _Session()

        handle_submit_artifact(
            session,
            workspace,
            {"artifact_type": artifact_type, "content": _VALID_CONTENT[artifact_type]},
        )

        artifact_file = artifact_dir / f"{artifact_type}.json"
        assert artifact_file.exists(), f"{artifact_type}: JSON artifact must exist after submit"

    @pytest.mark.parametrize("artifact_type", _ALL_ARTIFACT_TYPES)
    def test_valid_submit_json_wrapper_type_matches_artifact_type(
        self, artifact_type: str, tmp_path: Path
    ) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _Session()

        handle_submit_artifact(
            session,
            workspace,
            {"artifact_type": artifact_type, "content": _VALID_CONTENT[artifact_type]},
        )

        artifact_file = artifact_dir / f"{artifact_type}.json"
        wrapper = json.loads(artifact_file.read_text(encoding="utf-8"))
        assert wrapper.get("type") == artifact_type, (
            f"{artifact_type}: wrapper 'type' field must match artifact_type"
        )

    @pytest.mark.parametrize(
        "artifact_type",
        [at for at in _ALL_ARTIFACT_TYPES if at in HANDOFF_PATHS],
    )
    def test_valid_submit_produces_markdown_handoff(
        self, artifact_type: str, tmp_path: Path
    ) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _Session()

        handle_submit_artifact(
            session,
            workspace,
            {"artifact_type": artifact_type, "content": _VALID_CONTENT[artifact_type]},
        )

        handoff_rel = HANDOFF_PATHS[artifact_type]
        handoff_file = tmp_path / handoff_rel
        assert handoff_file.exists(), (
            f"{artifact_type}: Markdown handoff at {handoff_rel} must exist after submit"
        )

    @pytest.mark.parametrize("artifact_type", _ALL_ARTIFACT_TYPES)
    def test_rollback_on_markdown_failure_removes_json(
        self, artifact_type: str, tmp_path: Path
    ) -> None:
        if artifact_type not in HANDOFF_PATHS:
            pytest.skip(f"{artifact_type} has no markdown handoff — rollback N/A")

        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _Session()

        with (
            patch(
                "ralph.mcp.tools.artifact.sync_markdown_handoff",
                side_effect=OSError("simulated disk full"),
            ),
            pytest.raises(OSError),
        ):
            handle_submit_artifact(
                session,
                workspace,
                {"artifact_type": artifact_type, "content": _VALID_CONTENT[artifact_type]},
            )

        artifact_file = artifact_dir / f"{artifact_type}.json"
        assert not artifact_file.exists(), (
            f"{artifact_type}: JSON artifact must be rolled back when markdown write fails"
        )

    @pytest.mark.parametrize("artifact_type", _ALL_ARTIFACT_TYPES)
    def test_resubmit_after_failure_succeeds(self, artifact_type: str, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _Session()

        # First attempt: fails for types that have markdown handoffs
        if artifact_type in HANDOFF_PATHS:
            with (
                patch(
                    "ralph.mcp.tools.artifact.sync_markdown_handoff",
                    side_effect=OSError("simulated disk full"),
                ),
                pytest.raises(OSError),
            ):
                handle_submit_artifact(
                    session,
                    workspace,
                    {"artifact_type": artifact_type, "content": _VALID_CONTENT[artifact_type]},
                )

        # Second attempt: succeeds
        handle_submit_artifact(
            session,
            workspace,
            {"artifact_type": artifact_type, "content": _VALID_CONTENT[artifact_type]},
        )

        artifact_file = artifact_dir / f"{artifact_type}.json"
        assert artifact_file.exists(), (
            f"{artifact_type}: resubmission after failure must produce JSON artifact"
        )

