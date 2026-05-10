"""Integration test: same-workspace fan-out → analysis handoff.

Proves the full supported path:
  planning artifact with >=2 disjoint work_units
  → FanOutEffect from _determine_effect_from_policy
  → coordinator.run_fan_out produces ALL_WORKERS_COMPLETE
  → reducer advances to development_analysis (no merge/worktree step)
  → per-worker evidence stays in its own namespace

All workers use FakeAgentExecutor (no subprocess, no real MCP).
"""

from __future__ import annotations

import asyncio
import io
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

if TYPE_CHECKING:
    import pytest

from pathlib import Path

from rich.console import Console

from ralph.display.parallel_display import ParallelDisplay  # noqa: TC001
from ralph.mcp.server.factory import McpServerHandle
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutEffect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent, WorkerCompletedEvent, WorkerFailedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.parallel.mode import SameWorkspaceContext
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseParallelization
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

_DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"


def _make_work_unit(uid: str) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        allowed_directories=[f"src/{uid}"],
    )


def _make_policy_bundle(max_workers: int = 4) -> MagicMock:
    bundle = MagicMock()
    para = PhaseParallelization(max_parallel_workers=max_workers, post_fanout_verification=True)
    dev_phase = MagicMock(requires_commit=False, drain="development", role="execution")
    dev_phase.parallelization = para
    bundle.pipeline.phases = {"development": dev_phase}
    bundle.agents.agent_drains = {
        "development": MagicMock(chain="developer"),
    }
    bundle.agents.agent_chains = {
        "developer": MagicMock(agents=["developer"]),
    }
    return bundle


class _FakeDisplay:
    def __init__(self) -> None:
        self.console = Console(file=io.StringIO(), force_terminal=False, color_system=None)

    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status


@dataclass
class _RecordedHandle:
    handle: McpServerHandle
    shutdown_calls: int = 0


class _RecordingMcpFactory:
    def __init__(self) -> None:
        self.sessions: list[object] = []
        self.handles: list[_RecordedHandle] = []

    def build(self, session: object) -> McpServerHandle:
        self.sessions.append(session)
        recorded = _RecordedHandle(
            handle=McpServerHandle(
                endpoint=f"http://127.0.0.1:{10_000 + len(self.handles)}/mcp",
                pid=1000 + len(self.handles),
                shutdown=lambda: None,
            )
        )

        def _shutdown(record: _RecordedHandle = recorded) -> None:
            record.shutdown_calls += 1

        recorded.handle = McpServerHandle(
            endpoint=recorded.handle.endpoint,
            pid=recorded.handle.pid,
            shutdown=_shutdown,
        )
        self.handles.append(recorded)
        return recorded.handle


class RecordingDisplay:
    def __init__(self) -> None:
        self.statuses: dict[str, list[WorkerStatus]] = defaultdict(list)

    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        self.statuses[unit_id].append(status)


def _seed_artifact(repo_root: Path, unit_id: str) -> None:
    """Pre-populate worker-local artifact evidence so the coordinator's success check passes."""
    artifact_dir = repo_root / ".agent" / "workers" / unit_id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "development_result.json").write_text(
        json.dumps(
            {
                "name": "development_result",
                "type": "development_result",
                "content": {"summary": f"Worker {unit_id} done", "changes": []},
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "metadata": {},
            }
        )
    )


class TestSameWorkspaceFanOutE2E:
    """End-to-end test of the same-workspace parallel fan-out path."""

    def test_two_disjoint_units_emit_fan_out_effect(self) -> None:
        """_determine_effect_from_policy emits FanOutEffect for >=2 work units."""
        from ralph.config.models import UnifiedConfig  # noqa: PLC0415

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        state = PipelineState(phase="development", work_units=(unit_a, unit_b))
        policy_bundle = _make_policy_bundle(max_workers=2)

        effect = runner_module._determine_effect_from_policy(
            state, policy_bundle, config=UnifiedConfig()
        )

        assert isinstance(effect, FanOutEffect)
        assert {u.unit_id for u in effect.work_units} == {"unit-a", "unit-b"}
        assert effect.run_post_fanout_verification is True

    def test_single_unit_uses_serial_path(self) -> None:
        """A single work unit must NOT produce fan-out; it falls to the normal dev path."""
        from ralph.config.models import UnifiedConfig  # noqa: PLC0415

        state = PipelineState(
            phase="development", work_units=(_make_work_unit("unit-a"),)
        )
        policy_bundle = _make_policy_bundle(max_workers=4)

        effect = runner_module._determine_effect_from_policy(
            state, policy_bundle, config=UnifiedConfig()
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == "development"

    def test_fan_out_advances_to_development_analysis_after_all_succeed(self) -> None:
        """After ALL_WORKERS_COMPLETE, reducer advances phase to development_analysis."""
        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        units = (unit_a, unit_b)
        runs = {
            uid: FakeRun(outputs=[f"done-{uid}"], exit_code=0, duration_ms=1)
            for uid in ("unit-a", "unit-b")
        }
        effect = FanOutEffect(work_units=units, max_workers=2)
        initial_state = PipelineState(phase="development", work_units=units)

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(runs),
                display=cast("ParallelDisplay", _FakeDisplay()),
            )
        )

        assert PipelineEvent.ALL_WORKERS_COMPLETE in events

        policy_bundle = load_policy(_DEFAULT_POLICY_DIR)
        reduced_state = initial_state
        for event in events:
            reduced_state, _ = reducer_reduce(reduced_state, event, policy_bundle.pipeline)

        assert reduced_state.phase == "development_analysis", (
            f"Expected development_analysis after fan-out, got {reduced_state.phase!r}"
        )
        assert reduced_state.worker_states["unit-a"].status == WorkerStatus.SUCCEEDED
        assert reduced_state.worker_states["unit-b"].status == WorkerStatus.SUCCEEDED

    def test_worker_artifacts_are_namespaced_per_unit(self) -> None:
        """ALL_WORKERS_COMPLETE events carry isolated per-worker completion evidence."""
        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        units = (unit_a, unit_b)
        runs = {
            "unit-a": FakeRun(outputs=["result-a"], exit_code=0, duration_ms=1),
            "unit-b": FakeRun(outputs=["result-b"], exit_code=0, duration_ms=1),
        }
        effect = FanOutEffect(work_units=units, max_workers=2)

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(runs),
                display=cast("ParallelDisplay", _FakeDisplay()),
            )
        )

        completed = [e for e in events if isinstance(e, WorkerCompletedEvent)]
        completed_ids = {e.unit_id for e in completed}
        assert completed_ids == {"unit-a", "unit-b"}, (
            "Each worker must have its own completion event"
        )
        # Each unit_id appears exactly once in completed events — no cross-contamination
        for uid in ("unit-a", "unit-b"):
            unit_events = [e for e in completed if e.unit_id == uid]
            assert len(unit_events) == 1, (
                f"unit {uid!r} must have exactly one WorkerCompletedEvent, "
                f"got {len(unit_events)}"
            )

    def test_no_merge_step_required_for_supported_path(self) -> None:
        """The supported path transitions directly from fan-out to development_analysis
        without any git merge/worktree step.
        """
        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        units = (unit_a, unit_b)
        runs = {
            uid: FakeRun(outputs=[f"done-{uid}"], exit_code=0, duration_ms=1)
            for uid in ("unit-a", "unit-b")
        }
        effect = FanOutEffect(work_units=units, max_workers=2)
        initial_state = PipelineState(phase="development", work_units=units)

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(runs),
                display=cast("ParallelDisplay", _FakeDisplay()),
            )
        )

        policy_bundle = load_policy(_DEFAULT_POLICY_DIR)
        reduced_state = initial_state
        for event in events:
            reduced_state, _ = reducer_reduce(reduced_state, event, policy_bundle.pipeline)

        # Phase advanced to development_analysis — no merge/worktree event in the chain
        assert reduced_state.phase == "development_analysis"
        # Verify there are no merge-related intermediate phases
        git_merge_events = [
            e for e in events
            if hasattr(e, "name") and "merge" in str(e).lower()
        ]
        assert git_merge_events == [], (
            f"Supported path must not emit merge events, got: {git_merge_events}"
        )

    def test_two_workers_happy_path_artifact_only_success(self, tmp_path: Path) -> None:
        """Two safe workers complete with isolated artifact evidence in the same workspace.

        Uses a real SameWorkspaceContext with per-worker namespaces. Each worker's
        artifact directory is pre-seeded with evidence. Asserts no cross-namespace
        contamination and correct phase transition.
        """
        unit_a = WorkUnit(
            unit_id="unit-a", description="Unit A", allowed_directories=["src/a"]
        )
        unit_b = WorkUnit(
            unit_id="unit-b", description="Unit B", allowed_directories=["src/b"]
        )
        units = (unit_a, unit_b)

        # Pre-seed per-worker artifact evidence
        _seed_artifact(tmp_path, "unit-a")
        _seed_artifact(tmp_path, "unit-b")

        mcp_factory = _RecordingMcpFactory()
        ctx_module = __import__(
            "ralph.pipeline.parallel.coordinator", fromlist=["_WorkerContext"]
        )
        ctx = ctx_module._WorkerContext(
            same_workspace=SameWorkspaceContext(
                repo_root=tmp_path,
                mcp_factory=mcp_factory,
            )
        )

        runs = {
            "unit-a": FakeRun(outputs=["ok-a"], exit_code=0, duration_ms=1),
            "unit-b": FakeRun(outputs=["ok-b"], exit_code=0, duration_ms=1),
        }
        effect = FanOutEffect(work_units=units, max_workers=2)
        display = RecordingDisplay()
        initial_state = PipelineState(phase="development", work_units=units)

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(runs),
                display=display,
                ctx=ctx,
            )
        )

        # Both workers complete
        completed = [e for e in events if isinstance(e, WorkerCompletedEvent)]
        assert {e.unit_id for e in completed} == {"unit-a", "unit-b"}, (
            "Both workers must emit WorkerCompletedEvent"
        )
        # Pipeline event emitted
        assert PipelineEvent.ALL_WORKERS_COMPLETE in events, (
            "ALL_WORKERS_COMPLETE must be emitted after both workers succeed"
        )
        # No merge/worktree events
        merge_events = [
            e for e in events if hasattr(e, "name") and "merge" in str(e).lower()
        ]
        assert merge_events == [], f"No merge events expected, got {merge_events}"

        # Reducer advances to development_analysis
        policy_bundle = load_policy(_DEFAULT_POLICY_DIR)
        state = initial_state
        for event in events:
            state, _ = reducer_reduce(state, event, policy_bundle.pipeline)
        assert state.phase == "development_analysis"

        # Per-worker artifact directories exist and are separate
        artifact_a = tmp_path / ".agent" / "workers" / "unit-a" / "artifacts"
        artifact_b = tmp_path / ".agent" / "workers" / "unit-b" / "artifacts"
        assert artifact_a.exists() and any(artifact_a.iterdir()), (
            "unit-a must have its own artifact directory"
        )
        assert artifact_b.exists() and any(artifact_b.iterdir()), (
            "unit-b must have its own artifact directory"
        )
        # No cross-namespace contamination: unit-b's artifact not in unit-a's dir
        assert not (artifact_a / "unit-b").exists(), (
            "unit-b artifacts must not appear in unit-a's namespace"
        )
        assert not (artifact_b / "unit-a").exists(), (
            "unit-a artifacts must not appear in unit-b's namespace"
        )

    def test_out_of_scope_worker_write_is_denied(self, tmp_path: Path) -> None:
        """A worker attempting to write outside its allowed_directories is denied.

        The side_effect callback simulates a worker trying to write to a path
        outside its declared edit area. The scoped FsWorkspace raises ValueError,
        the coordinator wraps it in a WorkerFailedEvent, and no forbidden file
        is created on disk.
        """
        unit_a = WorkUnit(
            unit_id="unit-a", description="Unit A", allowed_directories=["src/a"]
        )
        units = (unit_a,)

        mcp_factory = _RecordingMcpFactory()
        ctx_module = __import__(
            "ralph.pipeline.parallel.coordinator", fromlist=["_WorkerContext"]
        )
        ctx = ctx_module._WorkerContext(
            same_workspace=SameWorkspaceContext(
                repo_root=tmp_path,
                mcp_factory=mcp_factory,
            )
        )

        # Build a scoped workspace that only allows src/a — used inside side_effect.
        worker_namespace = tmp_path / ".agent" / "workers" / "unit-a"
        scope = WorkspaceScope.for_same_workspace_worker(
            repo_root=tmp_path,
            allowed_directories=("src/a",),
            worker_namespace=worker_namespace,
        )
        scoped_ws = FsWorkspace(tmp_path, allowed_roots=scope.allowed_roots)

        def _attempt_forbidden_write() -> None:
            # This write targets src/b which is outside the allowed area.
            scoped_ws.write("src/b/forbidden.txt", "should not be written")

        runs = {
            "unit-a": FakeRun(
                outputs=[],
                exit_code=0,
                duration_ms=1,
                side_effect=_attempt_forbidden_write,
            ),
        }
        effect = FanOutEffect(work_units=units, max_workers=1)
        display = RecordingDisplay()

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(runs),
                display=display,
                ctx=ctx,
            )
        )

        # Worker should have failed
        failed = [e for e in events if isinstance(e, WorkerFailedEvent)]
        assert any(e.unit_id == "unit-a" for e in failed), (
            f"Expected WorkerFailedEvent for unit-a, got: {events}"
        )
        unit_a_failure = next(e for e in failed if e.unit_id == "unit-a")
        assert "outside workspace root" in unit_a_failure.error, (
            f"Error must mention 'outside workspace root', got: {unit_a_failure.error!r}"
        )

        # Forbidden file must not exist on disk
        forbidden = tmp_path / "src" / "b" / "forbidden.txt"
        assert not forbidden.exists(), (
            f"Forbidden file must not have been written: {forbidden}"
        )

        # No success completion
        assert PipelineEvent.ALL_WORKERS_COMPLETE not in events

    def test_repo_dirtiness_does_not_satisfy_missing_artifacts(
        self, tmp_path: Path
    ) -> None:
        """Repo-wide dirtiness from another worker CANNOT satisfy a worker's success check.

        Scenario:
        - unit-a has pre-seeded artifacts → succeeds
        - unit-b has NO artifacts → must fail
        - A real file is written to tmp_path/src/b/ to simulate repo dirtiness

        The coordinator must mark unit-b as FAILED with an error containing
        'produced no worker-local artifact evidence', even though the repo is
        dirty due to unit-a's (simulated) edits. Repo-wide git status is never
        a fallback success signal in same-workspace mode.
        """
        unit_a = WorkUnit(
            unit_id="unit-a", description="Unit A", allowed_directories=["src/a"]
        )
        unit_b = WorkUnit(
            unit_id="unit-b", description="Unit B", allowed_directories=["src/b"]
        )
        units = (unit_a, unit_b)

        # Only seed artifacts for unit-a; unit-b gets none on purpose.
        _seed_artifact(tmp_path, "unit-a")

        # Write a real file change into src/b to make the repo "dirty" — simulating
        # worker-b having edited files but not submitted any worker-local artifact.
        dirty_file = tmp_path / "src" / "b" / "some_file.py"
        dirty_file.parent.mkdir(parents=True, exist_ok=True)
        dirty_file.write_text("# simulated edit by worker-b\n")

        mcp_factory = _RecordingMcpFactory()
        ctx_module = __import__(
            "ralph.pipeline.parallel.coordinator", fromlist=["_WorkerContext"]
        )
        ctx = ctx_module._WorkerContext(
            same_workspace=SameWorkspaceContext(
                repo_root=tmp_path,
                mcp_factory=mcp_factory,
            )
        )

        runs = {
            "unit-a": FakeRun(outputs=["ok-a"], exit_code=0, duration_ms=1),
            "unit-b": FakeRun(outputs=["ok-b"], exit_code=0, duration_ms=1),
        }
        effect = FanOutEffect(work_units=units, max_workers=2)
        display = RecordingDisplay()

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(runs),
                display=display,
                ctx=ctx,
            )
        )

        # unit-b must have failed — repo dirtiness is not a success signal
        failed_events = [e for e in events if isinstance(e, WorkerFailedEvent)]
        unit_b_failures = [e for e in failed_events if e.unit_id == "unit-b"]
        assert len(unit_b_failures) == 1, (
            f"unit-b must emit exactly one WorkerFailedEvent even though the repo "
            f"is dirty, but got: {failed_events!r}"
        )
        assert "produced no worker-local artifact evidence" in unit_b_failures[0].error, (
            f"WorkerFailedEvent.error must contain 'produced no worker-local artifact evidence', "
            f"got: {unit_b_failures[0].error!r}. "
            "Repo-wide dirtiness must never substitute for per-worker artifact evidence."
        )

        # unit-a should still have succeeded (it had artifacts)
        completed_events = [e for e in events if isinstance(e, WorkerCompletedEvent)]
        assert any(e.unit_id == "unit-a" for e in completed_events), (
            "unit-a must have completed successfully (it had artifact evidence)"
        )


class TestRunnerAnalysisHandoffIntegration:
    """Runner-level integration: _execute_fan_out_sync wires the analysis handoff.

    These tests call _execute_fan_out_sync directly (not just the helper) to prove
    that after parallel fan-out the runner writes .agent/DEVELOPMENT_RESULT.md so the
    analysis phase can pick it up through the normal handoff path.
    """

    def test_runner_writes_development_result_md_after_all_succeed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After two workers succeed, .agent/DEVELOPMENT_RESULT.md must exist
        and contain the parallel summary so the analysis phase can read it."""
        from ralph.pipeline import checkpoint as ckpt  # noqa: PLC0415

        unit_a = WorkUnit(unit_id="unit-a", description="Unit A", allowed_directories=["src/a"])
        unit_b = WorkUnit(unit_id="unit-b", description="Unit B", allowed_directories=["src/b"])

        _seed_artifact(tmp_path, "unit-a")
        _seed_artifact(tmp_path, "unit-b")

        scope = WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))
        initial_state = PipelineState(
            phase="development",
            work_units=(unit_a, unit_b),
        )

        success_events = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerCompletedEvent(unit_id="unit-b", exit_code=0),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return success_events

        monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr(ckpt, "save", lambda state: None)

        bundle = _make_policy_bundle(max_workers=2)
        effect = FanOutEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=False,
        )

        runner_module._execute_fan_out_sync(
            effect=effect,
            state=initial_state,
            display=cast("ParallelDisplay", _FakeDisplay()),
            policy_bundle=bundle,
            workspace_scope=scope,
        )

        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists(), (
            ".agent/DEVELOPMENT_RESULT.md must be written for analysis to consume after fan-out"
        )
        content = handoff_path.read_text()
        assert "Parallel Development Summary" in content
        assert "unit-a" in content
        assert "unit-b" in content
        assert "all_succeeded: true" in content

    def test_runner_partial_failure_reflected_in_handoff(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When unit-b fails, DEVELOPMENT_RESULT.md must show any_failed=true
        and all_succeeded=false so the analysis phase sees the honest outcome."""
        from ralph.pipeline import checkpoint as ckpt  # noqa: PLC0415

        unit_a = WorkUnit(unit_id="unit-a", description="Unit A", allowed_directories=["src/a"])
        unit_b = WorkUnit(unit_id="unit-b", description="Unit B", allowed_directories=["src/b"])

        _seed_artifact(tmp_path, "unit-a")  # unit-b gets no artifact on purpose

        scope = WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))
        initial_state = PipelineState(
            phase="development",
            work_units=(unit_a, unit_b),
        )

        partial_events = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerFailedEvent(unit_id="unit-b", exit_code=1, error="no artifact evidence"),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return partial_events

        monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr(ckpt, "save", lambda state: None)

        bundle = _make_policy_bundle(max_workers=2)
        effect = FanOutEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=False,
        )

        runner_module._execute_fan_out_sync(
            effect=effect,
            state=initial_state,
            display=cast("ParallelDisplay", _FakeDisplay()),
            policy_bundle=bundle,
            workspace_scope=scope,
        )

        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists(), (
            ".agent/DEVELOPMENT_RESULT.md must be written even on partial failure"
        )
        content = handoff_path.read_text()
        assert "any_failed: true" in content
        assert "all_succeeded: false" in content
