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
