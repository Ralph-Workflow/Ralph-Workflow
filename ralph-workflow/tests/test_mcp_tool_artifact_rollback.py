"""Tests for artifact submit/rollback symmetry via _submit_ops_for_artifact."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_TYPE
from ralph.mcp.artifacts.handoffs import HANDOFF_PATHS
from ralph.mcp.artifacts.history import history_dir_for_artifact, history_index_path
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_TYPE
from ralph.mcp.artifacts.typed_artifacts import (
    TypedArtifactValidationError,
    normalize_analysis_decision_content,
)
from ralph.mcp.tools.artifact import (
    ArtifactHandlerDeps,
    _execute_ops_with_rollback,
    _submit_ops_for_artifact,
    _SubmitOp,
    handle_submit_artifact,
)
from ralph.mcp.tools.coordination import InvalidParamsError

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
    "fix_result": json.dumps(
        {"summary": "Fixed the regression", "files_changed": "- src/b.py"}
    ),
    "commit_message": json.dumps({"type": "commit", "subject": "feat: add per-type tests"}),
    "development_analysis_decision": json.dumps(
        {"status": "completed", "summary": "Analysis complete"}
    ),
    "review_analysis_decision": json.dumps(
        {"status": "completed", "summary": "Review approved"}
    ),
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


class _Session:
    session_id = "sess-1"

    def check_capability(self, cap: str) -> object:
        assert cap == "artifact.submit"
        return "approved"


class _DrainSession(_Session):
    def __init__(self, drain: str) -> None:
        self.drain = drain


class _Workspace:
    def __init__(self, root: Path) -> None:
        self._root = root

    def absolute_path(self, path: str) -> str:
        return str((self._root / path).resolve())


class TestExecuteOpsWithRollback:
    """Unit tests for the (op, undo) execution helper."""

    def test_all_ops_executed_in_order(self) -> None:
        log: list[str] = []
        ops = [
            _SubmitOp(run=lambda: log.append("op1"), undo=lambda: None),
            _SubmitOp(run=lambda: log.append("op2"), undo=lambda: None),
            _SubmitOp(run=lambda: log.append("op3"), undo=lambda: None),
        ]
        _execute_ops_with_rollback(ops)
        assert log == ["op1", "op2", "op3"]

    def test_rollback_in_reverse_order_on_failure(self) -> None:
        log: list[str] = []

        def fail_run() -> None:
            raise RuntimeError("boom")

        ops = [
            _SubmitOp(run=lambda: log.append("op1"), undo=lambda: log.append("undo1")),
            _SubmitOp(run=lambda: log.append("op2"), undo=lambda: log.append("undo2")),
            _SubmitOp(run=fail_run, undo=lambda: log.append("undo3_never")),
        ]
        with pytest.raises(RuntimeError):
            _execute_ops_with_rollback(ops)
        # op1 and op2 completed, so their undos run in reverse
        assert log == ["op1", "op2", "undo2", "undo1"]

    def test_only_completed_ops_rolled_back(self) -> None:
        log: list[str] = []

        def fail_run() -> None:
            raise RuntimeError("fail")

        ops = [
            _SubmitOp(run=fail_run, undo=lambda: log.append("undo_never")),
            _SubmitOp(run=lambda: None, undo=lambda: log.append("undo2_never")),
        ]
        with pytest.raises(RuntimeError):
            _execute_ops_with_rollback(ops)
        assert log == []

    def test_rollback_suppresses_undo_exceptions(self) -> None:
        def fail_undo() -> None:
            raise ValueError("undo error")

        def fail_run() -> None:
            raise RuntimeError("run error")

        ops = [
            _SubmitOp(run=lambda: None, undo=fail_undo),
            _SubmitOp(run=fail_run, undo=lambda: None),
        ]
        with pytest.raises(RuntimeError, match="run error"):
            _execute_ops_with_rollback(ops)


class TestSubmitOpsForArtifact:
    """Tests for op/undo pairs returned for each artifact type."""

    def test_commit_message_has_pre_submit_op_first(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        deps = ArtifactHandlerDeps()
        content = {"subject": "feat: x", "body": None, "trailer": None}

        ops = _submit_ops_for_artifact(
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
            "steps": [{"number": 1, "title": "t", "content": "c"}],
            "critical_files": {"primary_files": [], "reference_files": []},
            "risks_mitigations": [],
            "verification_strategy": [{"method": "m", "expected_outcome": "o"}],
        }

        ops = _submit_ops_for_artifact(
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

        ops = _submit_ops_for_artifact(
            "development_analysis_decision",
            tmp_path,
            artifact_dir,
            {"status": "completed", "summary": "done"},
            deps=deps,
        )

        # generic: main JSON + markdown = 2 ops
        assert len(ops) == _GENERIC_OP_COUNT


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


class TestPerArtifactTypeSubmission:
    """Parametrized per-artifact-type end-to-end submission tests.

    Covers: plan, development_result, issues, fix_result, commit_message,
    development_analysis_decision, review_analysis_decision.
    """

    @pytest.mark.parametrize("artifact_type", _ALL_ARTIFACT_TYPES)
    def test_valid_submit_produces_json_artifact(
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
    def test_resubmit_after_failure_succeeds(
        self, artifact_type: str, tmp_path: Path
    ) -> None:
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


class TestAnalysisDecisionValidationContract:
    """Tests that AnalysisDecision enforces the documented artifact contract."""

    def test_completed_without_remediation_is_valid(self) -> None:
        result = normalize_analysis_decision_content(
            {"status": "completed", "summary": "All good"}
        )
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
            normalize_analysis_decision_content(
                {"status": "unknown_status", "summary": "test"}
            )

    def test_legacy_synonyms_rejected_at_submission(self) -> None:
        for status in ("loopback", "retry", "approve", "approved", "reject"):
            with pytest.raises(TypedArtifactValidationError, match="status"):
                normalize_analysis_decision_content(
                    {"status": status, "summary": "test"}
                )


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


class TestHistoryIntegrationInSubmitOps:
    """Tests for artifact history archival integrated into _submit_ops_for_artifact."""

    def _now_iso(self) -> str:
        return "2026-05-06T12:00:00+00:00"

    def test_first_plan_submit_creates_current_and_history_snapshot(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _DrainSession("planning")

        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "plan",
                "content": _VALID_CONTENT["plan"],
            },
        )

        current_json = artifact_dir / "plan.json"
        current_md = tmp_path / HANDOFF_PATHS["plan"]
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        index_file = history_index_path(artifact_dir, "plan")
        json_archives = list(hist_dir.glob("*.json"))
        md_archives = list(hist_dir.glob("*.md"))

        assert current_json.exists()
        assert current_md.exists()
        assert index_file.exists()
        assert len(json_archives) >= 1, "first submit must create a history JSON snapshot"
        assert any(path.name != "index.md" for path in md_archives), (
            "first submit must create a history Markdown snapshot"
        )

    def test_first_analysis_submit_creates_current_and_history_snapshot(
        self,
        tmp_path: Path,
    ) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _DrainSession("planning_analysis")

        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "planning_analysis_decision",
                "content": _VALID_CONTENT["development_analysis_decision"],
            },
        )

        current_json = artifact_dir / "planning_analysis_decision.json"
        current_md = tmp_path / HANDOFF_PATHS["planning_analysis_decision"]
        hist_dir = history_dir_for_artifact(artifact_dir, "planning_analysis_decision")
        index_file = history_index_path(artifact_dir, "planning_analysis_decision")
        json_archives = list(hist_dir.glob("*.json"))
        md_archives = list(hist_dir.glob("*.md"))

        assert current_json.exists()
        assert current_md.exists()
        assert index_file.exists()
        assert len(json_archives) >= 1, "first analysis submit must create a history JSON snapshot"
        assert any(path.name != "index.md" for path in md_archives), (
            "first analysis submit must create a history Markdown snapshot"
        )

    def test_history_snapshot_uses_unique_timestamped_names_when_same_second_repeats(
        self, tmp_path: Path
    ) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _DrainSession("planning_analysis")
        deps = ArtifactHandlerDeps(now_iso=self._now_iso, history_enabled=True)

        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "planning_analysis_decision",
                "content": _VALID_CONTENT["development_analysis_decision"],
            },
            deps=deps,
        )
        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "planning_analysis_decision",
                "content": _VALID_CONTENT["development_analysis_decision"],
            },
            deps=deps,
        )

        hist_dir = history_dir_for_artifact(artifact_dir, "planning_analysis_decision")
        json_archives = sorted(hist_dir.glob("*.json"))
        md_archives = sorted(
            path for path in hist_dir.glob("*.md") if path.name != "index.md"
        )
        assert len(json_archives) == _EXPECTED_IMMEDIATE_HISTORY_SNAPSHOTS
        assert len(md_archives) == _EXPECTED_IMMEDIATE_HISTORY_SNAPSHOTS
        assert json_archives[0].name != json_archives[1].name
        assert md_archives[0].name != md_archives[1].name

    def test_history_disabled_does_not_prepend_extra_op(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        deps = ArtifactHandlerDeps(history_enabled=False)

        ops = _submit_ops_for_artifact(
            "development_analysis_decision",
            tmp_path,
            artifact_dir,
            {"status": "completed", "summary": "done"},
            deps=deps,
        )

        assert len(ops) == _GENERIC_OP_COUNT

    def test_history_enabled_appends_snapshot_op_to_plan(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        deps = ArtifactHandlerDeps(history_enabled=True)
        content: dict[str, object] = {
            "summary": {"context": "ctx", "scope_items": [{"text": "item"}]},
            "steps": [{"number": 1, "title": "t", "content": "c"}],
            "critical_files": {"primary_files": [], "reference_files": []},
            "risks_mitigations": [],
            "verification_strategy": [{"method": "m", "expected_outcome": "o"}],
        }

        ops = _submit_ops_for_artifact(
            PLAN_ARTIFACT_TYPE,
            tmp_path,
            artifact_dir,
            content,
            deps=deps,
        )

        # history snapshot op appended: main JSON + markdown + history + delete_draft
        assert len(ops) == _PLAN_OP_COUNT + 1

    def test_history_op_archives_existing_artifact(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.json").write_text('{"type":"plan","old":true}', encoding="utf-8")

        deps = ArtifactHandlerDeps(now_iso=self._now_iso, history_enabled=True)
        content = json.loads(_VALID_CONTENT["plan"])

        ops = _submit_ops_for_artifact(
            PLAN_ARTIFACT_TYPE,
            tmp_path,
            artifact_dir,
            content,
            deps=deps,
        )

        # Run artifact submit + handoff + history snapshot ops.
        for op in ops[:3]:
            op.run()

        hist_dir = history_dir_for_artifact(artifact_dir, PLAN_ARTIFACT_TYPE)
        json_archives = list(hist_dir.glob("*.json"))
        assert len(json_archives) == 1
        assert "Test plan submission" in json_archives[0].read_text(encoding="utf-8")

    def test_history_undo_removes_archived_files(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.json").write_text('{"type":"plan"}', encoding="utf-8")

        deps = ArtifactHandlerDeps(now_iso=self._now_iso, history_enabled=True)
        content = json.loads(_VALID_CONTENT["plan"])

        ops = _submit_ops_for_artifact(
            PLAN_ARTIFACT_TYPE,
            tmp_path,
            artifact_dir,
            content,
            deps=deps,
        )

        # Run artifact submit + handoff + history snapshot, then undo snapshot only.
        for op in ops[:3]:
            op.run()
        ops[2].undo()

        hist_dir = history_dir_for_artifact(artifact_dir, PLAN_ARTIFACT_TYPE)
        json_archives = list(hist_dir.glob("*.json"))
        assert json_archives == [], "history snapshot files must be removed after undo"
