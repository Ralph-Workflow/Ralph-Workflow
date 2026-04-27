"""Integration test: same-workspace fan-out → analysis handoff.

Proves the full supported path:
  planning artifact with >=2 disjoint work_units
  → FanOutDevelopmentEffect from _determine_effect_from_policy
  → coordinator.run_fan_out produces ALL_WORKERS_COMPLETE
  → reducer advances to development_analysis (no merge/worktree step)
  → per-worker evidence stays in its own namespace

All workers use FakeAgentExecutor (no subprocess, no real MCP).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_DEVELOPMENT_ANALYSIS
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutDevelopmentEffect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent, WorkerCompletedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun


def _make_work_unit(uid: str) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        allowed_directories=[f"src/{uid}"],
    )


def _make_policy_bundle(max_workers: int = 4) -> MagicMock:
    bundle = MagicMock()
    bundle.pipeline.phases = {
        PHASE_DEVELOPMENT: MagicMock(requires_commit=False, drain="development"),
    }
    bundle.pipeline.parallel_execution.max_parallel_workers = max_workers
    bundle.agents.agent_drains = {
        "development": MagicMock(chain="developer"),
    }
    bundle.agents.agent_chains = {
        "developer": MagicMock(agents=["developer"]),
    }
    return bundle


class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status


class TestSameWorkspaceFanOutE2E:
    """End-to-end test of the same-workspace parallel fan-out path."""

    def test_two_disjoint_units_emit_fan_out_effect(self) -> None:
        """_determine_effect_from_policy emits FanOutDevelopmentEffect for >=2 work units."""
        from ralph.config.models import UnifiedConfig  # noqa: PLC0415

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit_a, unit_b))
        policy_bundle = _make_policy_bundle(max_workers=2)

        effect = runner_module._determine_effect_from_policy(
            state, policy_bundle, config=UnifiedConfig()
        )

        assert isinstance(effect, FanOutDevelopmentEffect)
        assert {u.unit_id for u in effect.work_units} == {"unit-a", "unit-b"}
        assert effect.run_post_fanout_verification is True

    def test_single_unit_uses_serial_path(self) -> None:
        """A single work unit must NOT produce fan-out; it falls to the normal dev path."""
        from ralph.config.models import UnifiedConfig  # noqa: PLC0415

        state = PipelineState(
            phase=PHASE_DEVELOPMENT, work_units=(_make_work_unit("unit-a"),)
        )
        policy_bundle = _make_policy_bundle(max_workers=4)

        effect = runner_module._determine_effect_from_policy(
            state, policy_bundle, config=UnifiedConfig()
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == PHASE_DEVELOPMENT

    def test_fan_out_advances_to_development_analysis_after_all_succeed(self) -> None:
        """After ALL_WORKERS_COMPLETE, reducer advances phase to development_analysis."""
        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")
        units = (unit_a, unit_b)
        runs = {
            uid: FakeRun(outputs=[f"done-{uid}"], exit_code=0, duration_ms=1)
            for uid in ("unit-a", "unit-b")
        }
        effect = FanOutDevelopmentEffect(work_units=units, max_workers=2)
        initial_state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(runs),
                display=_FakeDisplay(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            )
        )

        assert PipelineEvent.ALL_WORKERS_COMPLETE in events

        reduced_state = initial_state
        for event in events:
            reduced_state, _ = reducer_reduce(reduced_state, event)

        assert reduced_state.phase == PHASE_DEVELOPMENT_ANALYSIS, (
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
        effect = FanOutDevelopmentEffect(work_units=units, max_workers=2)

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(runs),
                display=_FakeDisplay(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
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
        effect = FanOutDevelopmentEffect(work_units=units, max_workers=2)
        initial_state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(runs),
                display=_FakeDisplay(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            )
        )

        reduced_state = initial_state
        for event in events:
            reduced_state, _ = reducer_reduce(reduced_state, event)

        # Phase advanced to development_analysis — no merge/worktree event in the chain
        assert reduced_state.phase == PHASE_DEVELOPMENT_ANALYSIS
        # Verify there are no merge-related intermediate phases
        git_merge_events = [
            e for e in events
            if hasattr(e, "name") and "merge" in str(e).lower()
        ]
        assert git_merge_events == [], (
            f"Supported path must not emit merge events, got: {git_merge_events}"
        )
