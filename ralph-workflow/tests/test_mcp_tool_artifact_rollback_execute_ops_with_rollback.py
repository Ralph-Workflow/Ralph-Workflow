"""Tests for artifact submit/rollback symmetry via submit_ops_for_artifact."""

from __future__ import annotations

import json

import pytest

from ralph.mcp.tools.artifact import (
    SubmitOp,
    execute_ops_with_rollback,
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









class TestExecuteOpsWithRollback:
    """Unit tests for the (op, undo) execution helper."""

    def test_all_ops_executed_in_order(self) -> None:
        log: list[str] = []
        ops = [
            SubmitOp(run=lambda: log.append("op1"), undo=lambda: None),
            SubmitOp(run=lambda: log.append("op2"), undo=lambda: None),
            SubmitOp(run=lambda: log.append("op3"), undo=lambda: None),
        ]
        execute_ops_with_rollback(ops)
        assert log == ["op1", "op2", "op3"]

    def test_rollback_in_reverse_order_on_failure(self) -> None:
        log: list[str] = []

        def fail_run() -> None:
            raise RuntimeError("boom")

        ops = [
            SubmitOp(run=lambda: log.append("op1"), undo=lambda: log.append("undo1")),
            SubmitOp(run=lambda: log.append("op2"), undo=lambda: log.append("undo2")),
            SubmitOp(run=fail_run, undo=lambda: log.append("undo3_never")),
        ]
        with pytest.raises(RuntimeError):
            execute_ops_with_rollback(ops)
        # op1 and op2 completed, so their undos run in reverse
        assert log == ["op1", "op2", "undo2", "undo1"]

    def test_only_completed_ops_rolled_back(self) -> None:
        log: list[str] = []

        def fail_run() -> None:
            raise RuntimeError("fail")

        ops = [
            SubmitOp(run=fail_run, undo=lambda: log.append("undo_never")),
            SubmitOp(run=lambda: None, undo=lambda: log.append("undo2_never")),
        ]
        with pytest.raises(RuntimeError):
            execute_ops_with_rollback(ops)
        assert log == []

    def test_rollback_suppresses_undo_exceptions(self) -> None:
        def fail_undo() -> None:
            raise ValueError("undo error")

        def fail_run() -> None:
            raise RuntimeError("run error")

        ops = [
            SubmitOp(run=lambda: None, undo=fail_undo),
            SubmitOp(run=fail_run, undo=lambda: None),
        ]
        with pytest.raises(RuntimeError, match="run error"):
            execute_ops_with_rollback(ops)
