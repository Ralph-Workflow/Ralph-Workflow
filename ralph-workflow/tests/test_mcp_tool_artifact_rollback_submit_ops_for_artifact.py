"""Tests for artifact submit/rollback symmetry via submit_ops_for_artifact."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_TYPE
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_TYPE
from ralph.mcp.tools.artifact import (
    ArtifactHandlerDeps,
    submit_ops_for_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path

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


class TestSubmitOpsForArtifact:
    """Tests for op/undo pairs returned for each artifact type."""

    def test_commit_message_has_pre_submit_op_first(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        deps = ArtifactHandlerDeps()
        content = {"subject": "feat: x", "body": None, "trailer": None}

        ops = submit_ops_for_artifact(
            COMMIT_MESSAGE_TYPE,
            tmp_path,
            artifact_dir,
            content,
            deps=deps,
        )

        # commit_message: pre-submit + main JSON + markdown = 3 ops
        assert len(ops) == _COMMIT_MESSAGE_OP_COUNT

    def test_plan_has_delete_draft_as_last_op(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        deps = ArtifactHandlerDeps()
        content: dict[str, object] = {
            "summary": {
                "context": "ctx",
                "scope_items": [{"text": "item1"}],
            },
            "skills_mcp": {
                "skills": [
                    "test-driven-development",
                    "verification-before-completion",
                ],
                "mcps": [],
            },
            "steps": [{"number": 1, "title": "t", "content": "c"}],
            "critical_files": {"primary_files": [], "reference_files": []},
            "risks_mitigations": [],
            "verification_strategy": [{"method": "m", "expected_outcome": "o"}],
        }

        ops = submit_ops_for_artifact(
            PLAN_ARTIFACT_TYPE,
            tmp_path,
            artifact_dir,
            content,
            deps=deps,
        )

        # plan: main JSON + markdown + delete_draft = 3 ops
        assert len(ops) == _PLAN_OP_COUNT

    def test_generic_artifact_has_json_and_markdown_ops(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        deps = ArtifactHandlerDeps()

        ops = submit_ops_for_artifact(
            "development_analysis_decision",
            tmp_path,
            artifact_dir,
            {"status": "completed", "summary": "done"},
            deps=deps,
        )

        # generic: main JSON + markdown = 2 ops
        assert len(ops) == _GENERIC_OP_COUNT
