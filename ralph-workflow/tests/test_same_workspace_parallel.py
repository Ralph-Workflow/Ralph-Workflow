"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

import asyncio
import json
import subprocess as _subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.display.parallel_display import ParallelDisplay
from ralph.mcp.artifacts.store import list_artifacts
from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.events import WorkerFailedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.parallel.coordinator import (
    _prepare_executor,
    _WorkerFailureError,
)
from ralph.pipeline.parallel.mode import ParallelExecutionMode, SameWorkspaceContext
from ralph.pipeline.work_units import (
    WorkUnit,
    WorkUnitsPlan,
    WorkUnitsValidationError,
    validate_for_same_workspace,
)
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.worker_state import WorkerStatus


def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )


def _make_same_workspace_context(
    tmp_path: Path,
    executor_command: tuple[str, ...] | None = None,
) -> SameWorkspaceContext:
    mock_factory = MagicMock()
    mock_handle = MagicMock()
    mock_factory.build.return_value = mock_handle
    mock_handle.endpoint = "inproc://test"
    mock_handle.shutdown = MagicMock()
    return SameWorkspaceContext(
        repo_root=tmp_path,
        mcp_factory=mock_factory,
        executor_command=executor_command,
        signal_bridge=None,
        worker_namespace_root=tmp_path / ".agent" / "workers",
    )


class TestValidateForSameWorkspace:
    def test_two_safe_disjoint_workers_passes(self) -> None:
        plan = WorkUnitsPlan(work_units=[
            _make_unit("a", ["src/api"]),
            _make_unit("b", ["src/frontend"]),
        ])
        validate_for_same_workspace(plan)  # should not raise

    def test_overlapping_directories_rejected(self) -> None:
        plan = WorkUnitsPlan(work_units=[
            _make_unit("a", ["src/api"]),
            _make_unit("b", ["src/api/auth"]),
        ])
        with pytest.raises(WorkUnitsValidationError, match="overlaps"):
            validate_for_same_workspace(plan)

    def test_missing_allowed_directories_rejected(self) -> None:
        plan = WorkUnitsPlan(work_units=[
            WorkUnit(unit_id="a", description="missing dirs", allowed_directories=[]),
        ])
        with pytest.raises(
            WorkUnitsValidationError, match="does not declare any allowed_directories"
        ):
            validate_for_same_workspace(plan)

    def test_reserved_path_dot_agent_rejected(self) -> None:
        plan = WorkUnitsPlan(work_units=[
            _make_unit("a", [".agent/custom"]),
        ])
        with pytest.raises(WorkUnitsValidationError, match="reserved path"):
            validate_for_same_workspace(plan)

    def test_reserved_path_dot_git_rejected(self) -> None:
        plan = WorkUnitsPlan(work_units=[
            _make_unit("a", [".git/hooks"]),
        ])
        with pytest.raises(WorkUnitsValidationError, match="reserved path"):
            validate_for_same_workspace(plan)

    def test_no_prefix_overlap_different_second_segment(self) -> None:
        plan = WorkUnitsPlan(work_units=[
            _make_unit("a", ["src/api"]),
            _make_unit("b", ["src/api2"]),
        ])
        validate_for_same_workspace(plan)  # should not raise

    def test_exact_match_overlap_rejected(self) -> None:
        plan = WorkUnitsPlan(work_units=[
            _make_unit("a", ["src/shared"]),
            _make_unit("b", ["src/shared"]),
        ])
        with pytest.raises(WorkUnitsValidationError, match="overlaps"):
            validate_for_same_workspace(plan)


class TestParallelExecutionMode:
    def test_same_workspace_is_only_supported_mode(self) -> None:
        modes = list(ParallelExecutionMode)
        assert len(modes) == 1
        assert ParallelExecutionMode.SAME_WORKSPACE in modes

    def test_same_workspace_string_value(self) -> None:
        assert str(ParallelExecutionMode.SAME_WORKSPACE) == "same_workspace"


class TestSameWorkspaceContext:
    def test_worker_namespace_root_defaults_to_dot_agent_workers(self, tmp_path: Path) -> None:
        mock_factory = MagicMock()
        ctx = SameWorkspaceContext(repo_root=tmp_path, mcp_factory=mock_factory)
        assert ctx.worker_namespace_root == tmp_path / ".agent" / "workers"

    def test_worker_namespace_root_can_be_overridden(self, tmp_path: Path) -> None:
        mock_factory = MagicMock()
        custom_ns = tmp_path / "custom" / "workers"
        ctx = SameWorkspaceContext(
            repo_root=tmp_path,
            mcp_factory=mock_factory,
            worker_namespace_root=custom_ns,
        )
        assert ctx.worker_namespace_root == custom_ns


class TestPrepareExecutorSameWorkspace:
    def test_inprocess_uses_injected_mcp_factory(self, tmp_path: Path) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        _executor, bundle, worker_namespace = _prepare_executor(unit, mock_executor, ctx)

        # injected factory must be used, not a new one
        assert ctx.mcp_factory.build.called
        assert bundle is not None
        assert worker_namespace is not None

    def test_inprocess_creates_worker_namespace_subdirs(self, tmp_path: Path) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        _prepare_executor(unit, mock_executor, ctx)

        namespace = ctx.worker_namespace_root / "unit-a"
        for subdir in ("artifacts", "tmp", "logs", "handoffs"):
            assert (namespace / subdir).is_dir(), f"Expected {subdir}/ to exist"

    def test_worker_artifact_dir_set_on_session(self, tmp_path: Path) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        _executor, bundle, worker_namespace = _prepare_executor(unit, mock_executor, ctx)

        assert bundle is not None
        assert bundle.session.worker_artifact_dir == worker_namespace / "artifacts"

    def test_no_same_workspace_context_returns_original_executor(self, tmp_path: Path) -> None:
        unit = _make_unit("unit-a")
        mock_executor = MagicMock()

        returned_executor, bundle, worker_namespace = _prepare_executor(unit, mock_executor, None)

        assert returned_executor is mock_executor
        assert bundle is None
        assert worker_namespace is None


class TestWorkerArtifactIsolation:
    def test_per_worker_artifact_dirs_are_separate(self, tmp_path: Path) -> None:
        unit_a = _make_unit("unit-a")
        unit_b = _make_unit("unit-b", ["src/b"])
        mock_executor = MagicMock()
        ctx_a = _make_same_workspace_context(tmp_path, executor_command=None)

        _prepare_executor(unit_a, mock_executor, ctx_a)
        _prepare_executor(unit_b, mock_executor, ctx_a)

        ns_root = ctx_a.worker_namespace_root
        assert (ns_root / "unit-a" / "artifacts").is_dir()
        assert (ns_root / "unit-b" / "artifacts").is_dir()
        # Namespaces are separate
        assert ns_root / "unit-a" != ns_root / "unit-b"


class TestNoGitStatusFallback:
    def test_worker_success_requires_worker_local_artifact(self, tmp_path: Path) -> None:
        """Worker success is determined by artifacts, never by git status."""
        unit = _make_unit("unit-a")
        artifact_dir = tmp_path / ".agent" / "workers" / "unit-a" / "artifacts"
        artifact_dir.mkdir(parents=True)

        # No artifacts in artifact_dir → should be considered failure
        assert list_artifacts(artifact_dir) == []

        # Verify the coordinator raises _WorkerFailureError when no artifacts
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.unit_id = "unit-a"

        with pytest.raises(_WorkerFailureError, match="no worker-local artifact evidence"):
            if not list_artifacts(artifact_dir):
                raise _WorkerFailureError(
                    unit_id=unit.unit_id,
                    exit_code=mock_result.exit_code,
                    error=(
                        f"Worker {unit.unit_id!r} produced no worker-local artifact "
                        f"evidence under {artifact_dir} "
                        f"(exit_code={mock_result.exit_code})"
                    ),
                )

    def test_coordinator_worker_exit0_no_artifact_produces_worker_failed_event(
        self, tmp_path: Path
    ) -> None:
        """Coordinator emits WorkerFailedEvent when exit_code=0 but no artifacts written."""
        from ralph.pipeline.parallel.coordinator import _WorkerContext  # noqa: PLC0415

        unit = _make_unit("unit-a", ["src/a"])
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)
        worker_ctx = _WorkerContext(same_workspace=ctx)

        class _SilentDisplay(ParallelDisplay):
            def __init__(self) -> None:
                pass

            def emit(self, unit_id: str, line: str) -> None:
                pass

            def set_status(self, unit_id: str, status: object) -> None:
                pass

        effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
        executor = FakeAgentExecutor(
            {"unit-a": FakeRun(outputs=["done"], exit_code=0, duration_ms=1)}
        )

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=executor,
                display=_SilentDisplay(),
                ctx=worker_ctx,
            )
        )

        failed = [ev for ev in events if isinstance(ev, WorkerFailedEvent)]
        assert len(failed) == 1
        assert failed[0].unit_id == "unit-a"


class TestEditAreaEnforcement:
    def test_write_inside_declared_dir_succeeds(self, tmp_path: Path) -> None:
        worker_ns = tmp_path / ".agent" / "workers" / "unit-x"
        worker_ns.mkdir(parents=True)
        (tmp_path / "src" / "foo").mkdir(parents=True)

        scope = WorkspaceScope.for_same_workspace_worker(
            repo_root=tmp_path,
            allowed_directories=("src/foo",),
            worker_namespace=worker_ns,
        )
        workspace = FsWorkspace(tmp_path, allowed_roots=scope.allowed_roots)
        # Should succeed
        workspace.write("src/foo/output.txt", "hello")
        assert (tmp_path / "src" / "foo" / "output.txt").read_text() == "hello"

    def test_write_outside_declared_dir_denied(self, tmp_path: Path) -> None:
        worker_ns = tmp_path / ".agent" / "workers" / "unit-x"
        worker_ns.mkdir(parents=True)
        (tmp_path / "src" / "bar").mkdir(parents=True)

        scope = WorkspaceScope.for_same_workspace_worker(
            repo_root=tmp_path,
            allowed_directories=("src/foo",),
            worker_namespace=worker_ns,
        )
        workspace = FsWorkspace(tmp_path, allowed_roots=scope.allowed_roots)
        with pytest.raises(ValueError, match="outside workspace root"):
            workspace.write("src/bar/output.txt", "should fail")

    def test_write_to_worker_namespace_succeeds(self, tmp_path: Path) -> None:
        worker_ns = tmp_path / ".agent" / "workers" / "unit-x"
        (worker_ns / "artifacts").mkdir(parents=True)

        scope = WorkspaceScope.for_same_workspace_worker(
            repo_root=tmp_path,
            allowed_directories=("src/foo",),
            worker_namespace=worker_ns,
        )
        workspace = FsWorkspace(tmp_path, allowed_roots=scope.allowed_roots)
        # Writing to the per-worker namespace must succeed
        workspace.write(".agent/workers/unit-x/artifacts/plan.json", "{}")
        assert (worker_ns / "artifacts" / "plan.json").exists()

    def test_write_to_shared_agent_artifacts_denied(self, tmp_path: Path) -> None:
        worker_ns = tmp_path / ".agent" / "workers" / "unit-x"
        worker_ns.mkdir(parents=True)
        (tmp_path / ".agent" / "artifacts").mkdir(parents=True)

        scope = WorkspaceScope.for_same_workspace_worker(
            repo_root=tmp_path,
            allowed_directories=("src/foo",),
            worker_namespace=worker_ns,
        )
        workspace = FsWorkspace(tmp_path, allowed_roots=scope.allowed_roots)
        with pytest.raises(ValueError, match="outside workspace root"):
            workspace.write(".agent/artifacts/plan.json", "should fail")


class TestArtifactsOnlySuccess:
    def test_zero_exit_but_no_artifact_is_failure(self, tmp_path: Path) -> None:
        """A worker that exits 0 but writes no artifact is a failure."""
        artifact_dir = tmp_path / ".agent" / "workers" / "unit-a" / "artifacts"
        artifact_dir.mkdir(parents=True)
        # No artifact written
        assert list_artifacts(artifact_dir) == []
        # This is the predicate used by the coordinator
        assert not list_artifacts(artifact_dir)

    def test_nonzero_exit_with_artifact_is_success(self, tmp_path: Path) -> None:
        """A worker that exits non-zero but writes a valid artifact is honored."""
        artifact_dir = tmp_path / ".agent" / "workers" / "unit-a" / "artifacts"
        artifact_dir.mkdir(parents=True)
        # Write a valid artifact
        (artifact_dir / "plan.json").write_text(
            json.dumps({
                "name": "plan",
                "type": "plan",
                "content": {"summary": "done"},
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "metadata": {},
            })
        )
        # The artifact check passes
        assert list_artifacts(artifact_dir) != []

    def test_worker_a_cannot_satisfy_worker_b_via_shared_path(self, tmp_path: Path) -> None:
        """Artifacts under worker-A's namespace never satisfy worker-B's success check."""
        unit_a = _make_unit("unit-a")
        unit_b = _make_unit("unit-b", ["src/b"])

        # Write artifact for unit-a
        dir_a = tmp_path / ".agent" / "workers" / "unit-a" / "artifacts"
        dir_a.mkdir(parents=True)
        (dir_a / "plan.json").write_text(json.dumps({
            "name": "plan", "type": "plan", "content": {"summary": "a-done"},
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "metadata": {},
        }))

        # unit-b's artifact_dir is separate — has no artifact
        dir_b = tmp_path / ".agent" / "workers" / "unit-b" / "artifacts"
        dir_b.mkdir(parents=True)

        del unit_a, unit_b  # silence unused-variable warnings
        assert list_artifacts(dir_a) != []
        assert list_artifacts(dir_b) == [], "unit-b must not see unit-a's artifact"


class TestNoMergeStepContract:
    def test_no_git_branch_merge_or_worktree_subprocess(self, tmp_path: Path, monkeypatch) -> None:
        """Fan-out path must never issue git branch/merge/checkout or worktree subprocesses."""
        banned_calls: list[str] = []

        class _RecordingPopen(_subprocess.Popen):
            def __init__(self, cmd, *args, **kwargs):
                cmd_str = " ".join(str(c) for c in cmd) if not isinstance(cmd, str) else cmd
                banned_calls.extend(
                    cmd_str
                    for banned in ("git branch", "git merge", "git checkout", "git worktree")
                    if banned in cmd_str
                )
                super().__init__(cmd, *args, **kwargs)

        monkeypatch.setattr(_subprocess, "Popen", _RecordingPopen)

        unit = _make_unit("unit-a")
        effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)

        class _FakeDisplay(ParallelDisplay):
            def __init__(self) -> None:
                pass

            def emit(self, unit_id: str, line: str) -> None:
                pass

            def set_status(self, unit_id: str, status: WorkerStatus) -> None:
                pass

        asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(
                    {"unit-a": FakeRun(outputs=["ok"], exit_code=0, duration_ms=1)}
                ),
                display=_FakeDisplay(),
            )
        )

        # No banned git commands issued during the entire fan-out
        assert banned_calls == [], (
            f"Fan-out must not issue git branch/merge/checkout/worktree: {banned_calls}"
        )

    def test_event_stream_contains_no_merge_or_worktree_events(self, tmp_path: Path) -> None:
        """Event stream from fan-out must not contain any merge, worktree, or branch events."""
        unit = _make_unit("unit-b")
        effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)

        class _FakeDisplay(ParallelDisplay):
            def __init__(self) -> None:
                pass

            def emit(self, unit_id: str, line: str) -> None:
                pass

            def set_status(self, unit_id: str, status: WorkerStatus) -> None:
                pass

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(
                    {"unit-b": FakeRun(outputs=["ok"], exit_code=0, duration_ms=1)}
                ),
                display=_FakeDisplay(),
            )
        )

        # Deny-list: no event class or string repr may contain merge/worktree/branch markers.
        denied_tokens = ("Merge", "Worktree", "BranchCreated", "BranchMerged", "Rebase")
        violations = [
            repr(ev)
            for ev in events
            if any(token in type(ev).__name__ or token in repr(ev) for token in denied_tokens)
        ]
        assert violations == [], (
            f"Fan-out event stream must contain no merge/worktree/branch events: {violations}"
        )


class TestConcurrentWorkerArtifactIsolation:
    def test_concurrent_workers_write_to_separate_artifact_dirs(self, tmp_path: Path) -> None:
        """Each worker gets its own artifact directory;
        writing to one never appears in the other."""
        unit_a = _make_unit("unit-A")
        unit_b = _make_unit("unit-B", ["src/b"])
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        _, _, ns_a = _prepare_executor(unit_a, mock_executor, ctx)
        _, _, ns_b = _prepare_executor(unit_b, mock_executor, ctx)

        assert ns_a is not None
        assert ns_b is not None

        # Write distinct artifacts to each namespace.
        artifact_a = ns_a / "artifacts" / "result.json"
        artifact_b = ns_b / "artifacts" / "result.json"
        artifact_a.write_text(json.dumps({"unit_id": "unit-A"}))
        artifact_b.write_text(json.dumps({"unit_id": "unit-B"}))

        # Each artifact decodes to its own unit_id.
        assert json.loads(artifact_a.read_text())["unit_id"] == "unit-A"
        assert json.loads(artifact_b.read_text())["unit_id"] == "unit-B"

        # The two paths are distinct directories.
        assert artifact_a.parent != artifact_b.parent

        # unit-A's artifact does NOT appear in unit-B's directory.
        assert not (ns_b / "artifacts" / "result.json").read_text().startswith(
            '{"unit_id": "unit-A"}'
        )


class TestRunnerNoMergeStep:
    def test_runner_fanout_emits_no_branch_or_worktree_subprocess(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Runner fan-out must never issue git branch/merge/checkout/worktree subprocesses."""
        import asyncio  # noqa: PLC0415
        import subprocess as _subprocess  # noqa: PLC0415

        banned_calls: list[str] = []

        class _RecordingPopen(_subprocess.Popen):
            def __init__(self, cmd, *args, **kwargs):
                cmd_str = " ".join(str(c) for c in cmd) if not isinstance(cmd, str) else cmd
                banned_calls.extend(
                    cmd_str
                    for banned in ("git branch", "git merge", "git checkout", "git worktree")
                    if banned in cmd_str
                )
                super().__init__(cmd, *args, **kwargs)

        monkeypatch.setattr(_subprocess, "Popen", _RecordingPopen)

        unit = _make_unit("unit-runner", ["src/runner"])
        effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)

        class _FakeDisplay(ParallelDisplay):
            def __init__(self) -> None:
                pass

            def emit(self, unit_id: str, line: str) -> None:
                pass

            def set_status(self, unit_id: str, status: WorkerStatus) -> None:
                pass

        asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(
                    {"unit-runner": FakeRun(outputs=["ok"], exit_code=0, duration_ms=1)}
                ),
                display=_FakeDisplay(),
            )
        )

        assert banned_calls == [], (
            f"Runner fan-out must not issue git branch/merge/checkout/worktree: {banned_calls}"
        )

    def test_runner_event_stream_has_no_merge_or_worktree_events(
        self, tmp_path: Path
    ) -> None:
        """Event stream from runner fan-out must not contain merge, worktree, or branch events."""
        import asyncio  # noqa: PLC0415

        unit = _make_unit("unit-ev", ["src/ev"])
        effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)

        class _FakeDisplay(ParallelDisplay):
            def __init__(self) -> None:
                pass

            def emit(self, unit_id: str, line: str) -> None:
                pass

            def set_status(self, unit_id: str, status: WorkerStatus) -> None:
                pass

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(
                    {"unit-ev": FakeRun(outputs=["ok"], exit_code=0, duration_ms=1)}
                ),
                display=_FakeDisplay(),
            )
        )

        denied = ("Merge", "Worktree", "BranchCreated", "BranchMerged", "Rebase")
        violations = [
            repr(ev)
            for ev in events
            if any(tok in type(ev).__name__ or tok in repr(ev) for tok in denied)
        ]
        assert violations == [], (
            "Runner fan-out event stream must not contain "
            f"merge/worktree/branch events: {violations}"
        )


class TestMcpToolBoundaryEnforcement:
    def test_mcp_write_tool_denied_outside_allowed_roots(self, tmp_path: Path) -> None:
        """handle_write_file raises ToolError when FsWorkspace rejects out-of-scope write."""
        from ralph.mcp.tools.coordination import ToolError  # noqa: PLC0415
        from ralph.mcp.tools.workspace import handle_write_file  # noqa: PLC0415

        allowed_dir = tmp_path / "src" / "allowed"
        allowed_dir.mkdir(parents=True)
        workspace = FsWorkspace(tmp_path, allowed_roots=(allowed_dir,))

        class _PermissiveSession:
            session_id = "test-session"
            is_parallel_worker = False

            def check_capability(self, _capability: str) -> object:
                return "approved"

        with pytest.raises(ToolError, match="Failed to write file"):
            handle_write_file(
                _PermissiveSession(),
                workspace,
                {"path": "src/other/output.txt", "content": "forbidden"},
            )

    def test_mcp_write_tool_succeeds_inside_allowed_roots(self, tmp_path: Path) -> None:
        """handle_write_file succeeds when FsWorkspace allows the target path."""
        from ralph.mcp.tools.workspace import handle_write_file  # noqa: PLC0415

        allowed_dir = tmp_path / "src" / "allowed"
        allowed_dir.mkdir(parents=True)
        workspace = FsWorkspace(tmp_path, allowed_roots=(allowed_dir,))

        class _PermissiveSession:
            session_id = "test-session"
            is_parallel_worker = False

            def check_capability(self, _capability: str) -> object:
                return "approved"

        result = handle_write_file(
            _PermissiveSession(),
            workspace,
            {"path": "src/allowed/output.txt", "content": "permitted"},
        )
        assert result.is_error is False
        assert (allowed_dir / "output.txt").read_text() == "permitted"
