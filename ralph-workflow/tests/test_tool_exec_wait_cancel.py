"""Tests for exec_wait and exec_cancel MCP tool handlers (WU-4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.mcp.tools._exec_job_tools import (
    EXEC_CANCEL_TOOL,
    EXEC_WAIT_TOOL,
    handle_exec_cancel,
    handle_exec_wait,
)
from ralph.mcp.tools._exec_jobs import (
    ExecJobError,
    ExecJobRegistry,
    JobResult,
    JobSnapshot,
)
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolContent,
)
from ralph.process.manager._liveness_assessor import LivenessVerdict
from ralph.timeout_defaults import EXEC_SOFT_REPLY_BUDGET_MS
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot


class _MockExecJobRegistry(ExecJobRegistry):
    """Mock ExecJobRegistry for deterministic unit tests."""

    def __init__(
        self,
        *,
        wait_outcome: JobSnapshot | JobResult | Exception | None = None,
        cancel_outcome: JobResult | Exception | None = None,
        job_capabilities: dict[str, str] | None = None,
    ) -> None:
        self._wait_outcome = wait_outcome
        self._cancel_outcome = cancel_outcome
        self.waited_calls: list[dict[str, object]] = []
        self.cancelled_calls: list[dict[str, object]] = []
        self._job_capabilities = job_capabilities or {}

    def wait(
        self,
        job_id: str,
        *,
        wait_ms: int,
        capability: str,
        progress: object = None,
    ) -> JobSnapshot | JobResult:
        del progress
        self.waited_calls.append({"job_id": job_id, "wait_ms": wait_ms, "capability": capability})
        expected_cap = self._job_capabilities.get(job_id)
        if expected_cap is not None and expected_cap != capability:
            raise ExecJobError(
                job_id,
                f"capability mismatch: expected {expected_cap}, got {capability}",
            )
        if isinstance(self._wait_outcome, Exception):
            raise self._wait_outcome
        if self._wait_outcome is not None:
            return self._wait_outcome
        raise ExecJobError(
            job_id,
            "unknown or expired job; process was killed or lost in server restart",
        )

    def cancel(self, job_id: str, *, capability: str) -> JobResult:
        self.cancelled_calls.append({"job_id": job_id, "capability": capability})
        expected_cap = self._job_capabilities.get(job_id)
        if expected_cap is not None and expected_cap != capability:
            raise ExecJobError(
                job_id,
                f"capability mismatch: expected {expected_cap}, got {capability}",
            )
        if isinstance(self._cancel_outcome, Exception):
            raise self._cancel_outcome
        if self._cancel_outcome is not None:
            return self._cancel_outcome
        raise ExecJobError(
            job_id,
            "unknown or expired job; process was killed or lost in server restart",
        )


class TestExecWaitAndCancelHandlers:
    """Unit tests for handle_exec_wait and handle_exec_cancel."""

    def test_exec_wait_snapshot_when_still_running(self, tmp_path: Path) -> None:
        verdict = LivenessVerdict(
            score=0.2,
            pressure=0.1,
            stall_window_s=60.0,
            strongest_rule="",
            suspicious=False,
            verdict_kind="active",
            signals={"OutputRecent": 0.5},
            elapsed_s=12.5,
        )
        snapshot = JobSnapshot(
            job_id="job_active_123",
            running=True,
            elapsed_s=12.5,
            output={"stdout": "working...", "preview": "working..."},
            verdict=verdict,
            instruction="call exec_wait; do not re-run",
        )
        reg = _MockExecJobRegistry(wait_outcome=snapshot)
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        result = handle_exec_wait(
            session,
            workspace,
            {"job_id": "job_active_123", "wait_ms": 30000},
            registry=reg,
        )

        assert result.is_error is False
        assert len(result.content) == 1
        content = result.content[0]
        assert isinstance(content, ToolContent)
        data = json.loads(content.text)
        assert isinstance(data, dict)
        assert data["job_id"] == "job_active_123"
        assert data["running"] is True
        assert data["instruction"] == "call exec_wait; do not re-run"
        assert data["elapsed_s"] == 12.5
        verdict_data = data["verdict"]
        assert isinstance(verdict_data, dict)
        assert verdict_data["score"] == 0.2
        assert verdict_data["verdict_kind"] == "active"
        assert reg.waited_calls == [
            {"job_id": "job_active_123", "wait_ms": 30000, "capability": "ProcessExecBounded"}
        ]

    def test_exec_wait_final_result_success(self, tmp_path: Path) -> None:
        final_result = JobResult(
            job_id="job_done_456",
            returncode=0,
            output={"stdout": "success output", "preview": "success output"},
            kill_cause=None,
            verdict=None,
            elapsed_s=5.0,
            cancelled=False,
        )
        reg = _MockExecJobRegistry(wait_outcome=final_result)
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        result = handle_exec_wait(
            session,
            workspace,
            {"job_id": "job_done_456"},
            registry=reg,
        )

        assert result.is_error is False
        content = result.content[0]
        assert isinstance(content, ToolContent)
        data = json.loads(content.text)
        assert isinstance(data, dict)
        assert data["job_id"] == "job_done_456"
        assert data["returncode"] == 0
        assert data["running"] is False

    def test_exec_wait_final_result_nonzero_exit_is_error(self, tmp_path: Path) -> None:
        final_result = JobResult(
            job_id="job_failed_789",
            returncode=2,
            output={"stdout": "", "stderr": "command failed"},
            kill_cause=None,
            verdict=None,
            elapsed_s=3.0,
            cancelled=False,
        )
        reg = _MockExecJobRegistry(wait_outcome=final_result)
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        result = handle_exec_wait(
            session,
            workspace,
            {"job_id": "job_failed_789"},
            registry=reg,
        )

        assert result.is_error is True
        content = result.content[0]
        assert isinstance(content, ToolContent)
        data = json.loads(content.text)
        assert isinstance(data, dict)
        assert data["returncode"] == 2

    def test_exec_wait_kill_cause_stalled_renders_error(self, tmp_path: Path) -> None:
        killed_result = JobResult(
            job_id="job_stalled_101",
            returncode=None,
            output={"stdout": ""},
            kill_cause="stalled",
            verdict=None,
            elapsed_s=60.0,
        )
        reg = _MockExecJobRegistry(wait_outcome=killed_result)
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        result = handle_exec_wait(
            session,
            workspace,
            {"job_id": "job_stalled_101"},
            registry=reg,
        )

        assert result.is_error is True
        content = result.content[0]
        assert isinstance(content, ToolContent)
        text = content.text.lower()
        assert "stall" in text or "terminated: stalled" in text
        # Must not contain dead-end advice to run in background
        assert "run it in the background" not in text

    def test_exec_wait_kill_cause_hard_cap_renders_error(self, tmp_path: Path) -> None:
        killed_result = JobResult(
            job_id="job_hard_cap_102",
            returncode=None,
            output={"stdout": ""},
            kill_cause="hard_cap",
            verdict=None,
            elapsed_s=3600.0,
        )
        reg = _MockExecJobRegistry(wait_outcome=killed_result)
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        result = handle_exec_wait(
            session,
            workspace,
            {"job_id": "job_hard_cap_102"},
            registry=reg,
        )

        assert result.is_error is True
        content = result.content[0]
        assert isinstance(content, ToolContent)
        text = content.text.lower()
        assert "ceiling" in text
        assert "run it in the background" not in text

    def test_exec_wait_unknown_or_expired_job_id_returns_is_error(self, tmp_path: Path) -> None:
        reg = _MockExecJobRegistry(
            wait_outcome=ExecJobError(
                "job_nonexistent",
                "unknown or expired job; process was killed or lost in server restart",
            )
        )
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        result = handle_exec_wait(
            session,
            workspace,
            {"job_id": "job_nonexistent"},
            registry=reg,
        )

        assert result.is_error is True
        content = result.content[0]
        assert isinstance(content, ToolContent)
        assert "unknown or expired job" in content.text

    def test_exec_wait_capability_mismatch_returns_is_error(self, tmp_path: Path) -> None:
        reg = _MockExecJobRegistry(job_capabilities={"job_unbounded": "ProcessExecUnbounded"})
        # Session only holds bounded capability
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        result = handle_exec_wait(
            session,
            workspace,
            {"job_id": "job_unbounded"},
            registry=reg,
        )

        assert result.is_error is True
        content = result.content[0]
        assert isinstance(content, ToolContent)
        assert "capability mismatch" in content.text

    def test_exec_wait_no_capability_raises_capability_denied(self, tmp_path: Path) -> None:
        session = MockSession(set())
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(CapabilityDeniedError):
            handle_exec_wait(session, workspace, {"job_id": "job_123"})

    def test_exec_wait_invalid_params_empty_job_id(self, tmp_path: Path) -> None:
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(InvalidParamsError, match="'job_id'"):
            handle_exec_wait(session, workspace, {"job_id": ""})

    def test_exec_wait_clamps_wait_ms_to_soft_reply_budget(self, tmp_path: Path) -> None:
        snapshot = JobSnapshot(
            job_id="job_123",
            running=True,
            elapsed_s=1.0,
            output={},
            verdict=None,
            instruction="call exec_wait; do not re-run",
        )
        reg = _MockExecJobRegistry(wait_outcome=snapshot)
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        handle_exec_wait(
            session,
            workspace,
            {"job_id": "job_123", "wait_ms": 999_999},
            registry=reg,
        )

        assert reg.waited_calls[0]["wait_ms"] == EXEC_SOFT_REPLY_BUDGET_MS

    # --- Exec Cancel Tests ---

    def test_exec_cancel_running_job_succeeds(self, tmp_path: Path) -> None:
        cancel_result = JobResult(
            job_id="job_running_to_cancel",
            returncode=None,
            output={"stdout": "partial before cancel"},
            kill_cause=None,
            verdict=None,
            elapsed_s=15.0,
            cancelled=True,
        )
        reg = _MockExecJobRegistry(cancel_outcome=cancel_result)
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        result = handle_exec_cancel(
            session,
            workspace,
            {"job_id": "job_running_to_cancel"},
            registry=reg,
        )

        assert result.is_error is False
        content = result.content[0]
        assert isinstance(content, ToolContent)
        data = json.loads(content.text)
        assert isinstance(data, dict)
        assert data["job_id"] == "job_running_to_cancel"
        assert data["cancelled"] is True
        assert data["elapsed_s"] == 15.0
        assert reg.cancelled_calls == [
            {"job_id": "job_running_to_cancel", "capability": "ProcessExecBounded"}
        ]

    def test_exec_cancel_unknown_job_id_returns_is_error(self, tmp_path: Path) -> None:
        reg = _MockExecJobRegistry(
            cancel_outcome=ExecJobError("job_lost", "unknown or expired job")
        )
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        result = handle_exec_cancel(
            session,
            workspace,
            {"job_id": "job_lost"},
            registry=reg,
        )

        assert result.is_error is True
        content = result.content[0]
        assert isinstance(content, ToolContent)
        assert "unknown or expired job" in content.text

    def test_exec_cancel_capability_mismatch_returns_is_error(self, tmp_path: Path) -> None:
        reg = _MockExecJobRegistry(job_capabilities={"job_unbounded": "ProcessExecUnbounded"})
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        result = handle_exec_cancel(
            session,
            workspace,
            {"job_id": "job_unbounded"},
            registry=reg,
        )

        assert result.is_error is True
        content = result.content[0]
        assert isinstance(content, ToolContent)
        assert "capability mismatch" in content.text

    def test_exec_cancel_no_capability_raises_capability_denied(self, tmp_path: Path) -> None:
        session = MockSession(set())
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(CapabilityDeniedError):
            handle_exec_cancel(session, workspace, {"job_id": "job_123"})

    def test_exec_cancel_invalid_params_missing_job_id(self, tmp_path: Path) -> None:
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(InvalidParamsError, match="'job_id'"):
            handle_exec_cancel(session, workspace, {})


def test_tool_constants() -> None:
    """Tool name constants match canonical tool names."""
    assert EXEC_WAIT_TOOL == "exec_wait"
    assert EXEC_CANCEL_TOOL == "exec_cancel"
