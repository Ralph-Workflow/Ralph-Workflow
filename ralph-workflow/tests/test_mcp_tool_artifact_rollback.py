"""Tests for artifact submit/rollback symmetry via _submit_ops_for_artifact."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_TYPE
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_TYPE
from ralph.mcp.tools.artifact import (
    ArtifactHandlerDeps,
    _execute_ops_with_rollback,
    _submit_ops_for_artifact,
    _SubmitOp,
    handle_submit_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path

_COMMIT_MESSAGE_OP_COUNT = 3
_PLAN_OP_COUNT = 3
_GENERIC_OP_COUNT = 2


class _Session:
    session_id = "sess-1"

    def check_capability(self, cap: str) -> object:
        assert cap == "artifact.submit"
        return "approved"


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
