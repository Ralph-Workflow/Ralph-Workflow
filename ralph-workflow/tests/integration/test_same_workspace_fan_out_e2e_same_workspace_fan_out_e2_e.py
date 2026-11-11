from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
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
from tests.integration._same_workspace_fan_out_e2e_fake_display import _FakeDisplay
from tests.integration._same_workspace_fan_out_e2e_recording_display import RecordingDisplay
from tests.integration._same_workspace_fan_out_e2e_recording_mcp_factory import (
    _RecordingMcpFactory,
)
from tests.plan_fixtures import development_result_markdown

_DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"


def _seed_artifact(tmp_path: Path, unit_id: str) -> None:
    artifact_dir = tmp_path / ".agent" / "workers" / unit_id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "development_result.md").write_text(
        development_result_markdown(unit_id),
        encoding="utf-8",
    )


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


class TestSameWorkspaceFanOutE2E:
    def test_two_disjoint_units_emit_fan_out_effect(self) -> None:

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        state = PipelineState(phase="development", work_units=(unit_a, unit_b))
        policy_bundle = _make_policy_bundle(max_workers=2)

        effect = runner_module.determine_effect_from_policy(
            state, policy_bundle, config=UnifiedConfig()
        )

        assert isinstance(effect, FanOutEffect)
        assert {u.unit_id for u in effect.work_units} == {"unit-a", "unit-b"}
        assert effect.run_post_fanout_verification is True

    def test_single_unit_uses_serial_path(self) -> None:

        state = PipelineState(phase="development", work_units=(_make_work_unit("unit-a"),))
        policy_bundle = _make_policy_bundle(max_workers=4)

        effect = runner_module.determine_effect_from_policy(
            state, policy_bundle, config=UnifiedConfig()
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == "development"

    def test_fan_out_advances_to_development_commit_review_after_all_succeed(self) -> None:
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
                display=_FakeDisplay(),
            )
        )

        assert PipelineEvent.ALL_WORKERS_COMPLETE in events

        policy_bundle = load_policy(_DEFAULT_POLICY_DIR)
        reduced_state = initial_state
        for event in events:
            reduced_state, _ = reducer_reduce(reduced_state, event, policy_bundle.pipeline)

        assert reduced_state.phase == "development_commit_review", (
            f"Expected development_commit_review after fan-out, got {reduced_state.phase!r}"
        )
        assert reduced_state.worker_states["unit-a"].status == WorkerStatus.SUCCEEDED
        assert reduced_state.worker_states["unit-b"].status == WorkerStatus.SUCCEEDED

    def test_worker_artifacts_are_namespaced_per_unit(self) -> None:
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
                display=_FakeDisplay(),
            )
        )

        completed = [e for e in events if isinstance(e, WorkerCompletedEvent)]
        completed_ids = {e.unit_id for e in completed}
        assert completed_ids == {"unit-a", "unit-b"}, (
            "Each worker must have its own completion event"
        )
        for uid in ("unit-a", "unit-b"):
            unit_events = [e for e in completed if e.unit_id == uid]
            assert len(unit_events) == 1, (
                f"unit {uid!r} must have exactly one WorkerCompletedEvent, got {len(unit_events)}"
            )

    def test_no_merge_step_required_for_supported_path(self) -> None:
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
                display=_FakeDisplay(),
            )
        )

        policy_bundle = load_policy(_DEFAULT_POLICY_DIR)
        reduced_state = initial_state
        for event in events:
            reduced_state, _ = reducer_reduce(reduced_state, event, policy_bundle.pipeline)

        assert reduced_state.phase == "development_commit_review"
        git_merge_events = [e for e in events if hasattr(e, "name") and "merge" in str(e).lower()]
        assert git_merge_events == [], (
            f"Supported path must not emit merge events, got: {git_merge_events}"
        )

    def test_two_workers_happy_path_artifact_only_success(self, tmp_path: Path) -> None:
        unit_a = WorkUnit(unit_id="unit-a", description="Unit A", allowed_directories=["src/a"])
        unit_b = WorkUnit(unit_id="unit-b", description="Unit B", allowed_directories=["src/b"])
        units = (unit_a, unit_b)

        _seed_artifact(tmp_path, "unit-a")
        _seed_artifact(tmp_path, "unit-b")

        mcp_factory = _RecordingMcpFactory()
        ctx_module = __import__("ralph.pipeline.parallel.coordinator", fromlist=["WorkerContext"])
        ctx = ctx_module.WorkerContext(
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

        completed = [e for e in events if isinstance(e, WorkerCompletedEvent)]
        assert {e.unit_id for e in completed} == {"unit-a", "unit-b"}, (
            "Both workers must emit WorkerCompletedEvent"
        )
        assert PipelineEvent.ALL_WORKERS_COMPLETE in events, (
            "ALL_WORKERS_COMPLETE must be emitted after both workers succeed"
        )
        merge_events = [e for e in events if hasattr(e, "name") and "merge" in str(e).lower()]
        assert merge_events == [], f"No merge events expected, got {merge_events}"

        policy_bundle = load_policy(_DEFAULT_POLICY_DIR)
        state = initial_state
        for event in events:
            state, _ = reducer_reduce(state, event, policy_bundle.pipeline)
        assert state.phase == "development_commit_review"

        artifact_a = tmp_path / ".agent" / "workers" / "unit-a" / "artifacts"
        artifact_b = tmp_path / ".agent" / "workers" / "unit-b" / "artifacts"
        assert artifact_a.exists() and any(artifact_a.iterdir()), (
            "unit-a must have its own artifact directory"
        )
        assert artifact_b.exists() and any(artifact_b.iterdir()), (
            "unit-b must have its own artifact directory"
        )
        assert not (artifact_a / "unit-b").exists(), (
            "unit-b artifacts must not appear in unit-a's namespace"
        )
        assert not (artifact_b / "unit-a").exists(), (
            "unit-a artifacts must not appear in unit-b's namespace"
        )

    def test_out_of_scope_worker_write_is_denied(self, tmp_path: Path) -> None:
        unit_a = WorkUnit(unit_id="unit-a", description="Unit A", allowed_directories=["src/a"])
        units = (unit_a,)

        mcp_factory = _RecordingMcpFactory()
        ctx_module = __import__("ralph.pipeline.parallel.coordinator", fromlist=["WorkerContext"])
        ctx = ctx_module.WorkerContext(
            same_workspace=SameWorkspaceContext(
                repo_root=tmp_path,
                mcp_factory=mcp_factory,
            )
        )

        worker_namespace = tmp_path / ".agent" / "workers" / "unit-a"
        scope = WorkspaceScope.for_same_workspace_worker(
            repo_root=tmp_path,
            allowed_directories=("src/a",),
            worker_namespace=worker_namespace,
        )
        scoped_ws = FsWorkspace(tmp_path, allowed_roots=scope.allowed_roots)

        def _attempt_forbidden_write() -> None:
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

        failed = [e for e in events if isinstance(e, WorkerFailedEvent)]
        assert any(e.unit_id == "unit-a" for e in failed), (
            f"Expected WorkerFailedEvent for unit-a, got: {events}"
        )
        unit_a_failure = next(e for e in failed if e.unit_id == "unit-a")
        assert "outside workspace root" in unit_a_failure.error, (
            f"Error must mention 'outside workspace root', got: {unit_a_failure.error!r}"
        )

        forbidden = tmp_path / "src" / "b" / "forbidden.txt"
        assert not forbidden.exists(), f"Forbidden file must not have been written: {forbidden}"

        assert PipelineEvent.ALL_WORKERS_COMPLETE not in events

    def test_repo_dirtiness_does_not_satisfy_missing_artifacts(self, tmp_path: Path) -> None:
        unit_a = WorkUnit(unit_id="unit-a", description="Unit A", allowed_directories=["src/a"])
        unit_b = WorkUnit(unit_id="unit-b", description="Unit B", allowed_directories=["src/b"])
        units = (unit_a, unit_b)

        _seed_artifact(tmp_path, "unit-a")

        dirty_file = tmp_path / "src" / "b" / "some_file.py"
        dirty_file.parent.mkdir(parents=True, exist_ok=True)
        dirty_file.write_text("# simulated edit by worker-b\n")

        mcp_factory = _RecordingMcpFactory()
        ctx_module = __import__("ralph.pipeline.parallel.coordinator", fromlist=["WorkerContext"])
        ctx = ctx_module.WorkerContext(
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

        completed_events = [e for e in events if isinstance(e, WorkerCompletedEvent)]
        assert any(e.unit_id == "unit-a" for e in completed_events), (
            "unit-a must have completed successfully (it had artifact evidence)"
        )
