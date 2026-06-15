"""Tests for runner-boundary fail-closed and explicit mode selection for parallel workers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import ExitFailureEffect, FanOutEffect, InvokeAgentEffect
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseParallelization

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _legacy_fan_out_policy_bundle() -> PolicyBundle:
    """Bundle that opts into the legacy ``ralph_fan_out`` dispatch mode.

    The bundled default in ``ralph/policy/defaults/pipeline.toml`` sets
    ``dispatch_mode = 'agent_subagents'`` on the development phase, so
    the existing routing tests that assert ``FanOutEffect`` (or rely on
    the same-workspace preflight validation that runs only on the
    ``ralph_fan_out`` path) must opt into the legacy path explicitly on
    their policy fixture.
    """
    bundle = _load_default_policy_bundle()
    dev_phase = bundle.pipeline.phases["development"]
    assert dev_phase.parallelization is not None
    legacy_parallelization = dev_phase.parallelization.model_copy(
        update={"dispatch_mode": "ralph_fan_out"}
    )
    legacy_dev_phase = dev_phase.model_copy(update={"parallelization": legacy_parallelization})
    legacy_phases = dict(bundle.pipeline.phases)
    legacy_phases["development"] = legacy_dev_phase
    return bundle.model_copy(
        update={"pipeline": bundle.pipeline.model_copy(update={"phases": legacy_phases})}
    )


class TestRunnerBoundaryPreflightRejection:
    def test_runner_rejects_overlapping_work_units(self) -> None:
        bundle = _legacy_fan_out_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/api"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/api/auth"]),
            ),
        )
        effect = runner_module.determine_effect_from_policy(state, bundle)
        assert isinstance(effect, ExitFailureEffect)
        assert "parallel preflight rejected plan:" in effect.reason

    def test_runner_rejects_missing_allowed_directories(self) -> None:
        bundle = _legacy_fan_out_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=[]),
            ),
        )
        effect = runner_module.determine_effect_from_policy(state, bundle)
        assert isinstance(effect, ExitFailureEffect)
        assert "parallel preflight rejected plan:" in effect.reason

    def test_runner_rejects_reserved_path_dot_agent(self) -> None:
        bundle = _legacy_fan_out_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=[".agent/custom"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
            ),
        )
        effect = runner_module.determine_effect_from_policy(state, bundle)
        assert isinstance(effect, ExitFailureEffect)
        assert "parallel preflight rejected plan:" in effect.reason

    def test_runner_constructs_fan_out_effect_when_safe(self) -> None:
        bundle = _legacy_fan_out_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
            ),
        )
        effect = runner_module.determine_effect_from_policy(state, bundle)
        assert isinstance(effect, FanOutEffect)
        assert {u.unit_id for u in effect.work_units} == {"unit-a", "unit-b"}

    def test_runner_does_not_fall_back_to_single_worker(self) -> None:
        """When validation fails, runner must NOT degrade to single InvokeAgentEffect."""
        bundle = _legacy_fan_out_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/shared"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/shared"]),
            ),
        )
        effect = runner_module.determine_effect_from_policy(state, bundle)
        assert not isinstance(effect, InvokeAgentEffect), (
            "Rejected parallel plan must not fall back to a single development invocation"
        )
        assert isinstance(effect, ExitFailureEffect)

    def test_runner_single_work_unit_does_not_trigger_validation(self) -> None:
        """Single work unit must bypass fan-out validation and run normal serial path."""
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(WorkUnit(unit_id="unit-a", description="A"),),
        )
        effect = runner_module.determine_effect_from_policy(state, bundle)
        assert isinstance(effect, InvokeAgentEffect), (
            "Single work unit must use normal serial development (no fan-out)"
        )

    def test_runner_post_fanout_verification_defaults_to_false(self) -> None:
        """FanOutEffect.run_post_fanout_verification must default to False."""
        bundle = _legacy_fan_out_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
            ),
        )
        effect = runner_module.determine_effect_from_policy(state, bundle)
        assert isinstance(effect, FanOutEffect)
        assert effect.run_post_fanout_verification is False, (
            "run_post_fanout_verification must default to False so tests never run make verify"
        )

    def test_runner_rejects_fan_out_when_phase_has_no_parallelization_policy(self) -> None:
        """Fan-out must fail closed when the active phase has no parallelization policy."""
        bundle = _load_default_policy_bundle()
        # planning phase has no parallelization declared
        state = PipelineState(
            phase="planning",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
            ),
        )
        effect = runner_module.determine_effect_from_policy(state, bundle)
        assert isinstance(effect, ExitFailureEffect)
        assert "does not declare parallelization" in effect.reason

    def test_runner_uses_phase_scoped_max_parallel_workers(self) -> None:
        """FanOutEffect must use max_workers from the phase's parallelization."""

        bundle = MagicMock()
        # Set up a development phase with parallelization, max_workers=1
        para = PhaseParallelization(max_parallel_workers=1, post_fanout_verification=False)
        dev_phase = MagicMock()
        dev_phase.parallelization = para
        dev_phase.requires_commit = False
        bundle.pipeline.phases.get.return_value = dev_phase
        bundle.pipeline.terminal_phase = "complete"

        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
            ),
        )
        effect = runner_module.determine_effect_from_policy(state, bundle)
        assert isinstance(effect, FanOutEffect)
        assert effect.max_workers == 1

    def test_runner_post_fanout_verification_reads_phase_scoped_value(self) -> None:
        """FanOutEffect.run_post_fanout_verification reads from phase parallelization."""

        bundle = MagicMock()
        para = PhaseParallelization(max_parallel_workers=8, post_fanout_verification=True)
        dev_phase = MagicMock()
        dev_phase.parallelization = para
        dev_phase.requires_commit = False
        bundle.pipeline.phases.get.return_value = dev_phase
        bundle.pipeline.terminal_phase = "complete"

        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
            ),
        )
        effect = runner_module.determine_effect_from_policy(state, bundle)
        assert isinstance(effect, FanOutEffect)
        assert effect.run_post_fanout_verification is True
