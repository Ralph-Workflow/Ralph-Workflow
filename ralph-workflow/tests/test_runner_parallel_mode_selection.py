"""Tests for runner-boundary fail-closed and explicit mode selection for parallel workers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import ExitFailureEffect, FanOutDevelopmentEffect, InvokeAgentEffect
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


class TestRunnerBoundaryPreflightRejection:
    def test_runner_rejects_overlapping_work_units(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/api"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/api/auth"]),
            ),
        )
        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, ExitFailureEffect)
        assert "parallel preflight rejected plan:" in effect.reason

    def test_runner_rejects_missing_allowed_directories(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=[]),
            ),
        )
        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, ExitFailureEffect)
        assert "parallel preflight rejected plan:" in effect.reason

    def test_runner_rejects_reserved_path_dot_agent(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=[".agent/custom"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
            ),
        )
        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, ExitFailureEffect)
        assert "parallel preflight rejected plan:" in effect.reason

    def test_runner_constructs_fan_out_effect_when_safe(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
            ),
        )
        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, FanOutDevelopmentEffect)
        assert {u.unit_id for u in effect.work_units} == {"unit-a", "unit-b"}

    def test_runner_does_not_fall_back_to_single_worker(self) -> None:
        """When validation fails, runner must NOT degrade to single InvokeAgentEffect."""
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/shared"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/shared"]),
            ),
        )
        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert not isinstance(effect, InvokeAgentEffect), (
            "Rejected parallel plan must not fall back to a single development invocation"
        )
        assert isinstance(effect, ExitFailureEffect)

    def test_runner_single_work_unit_does_not_trigger_validation(self) -> None:
        """Single work unit must bypass fan-out validation and run normal serial path."""
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A"),
            ),
        )
        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, InvokeAgentEffect), (
            "Single work unit must use normal serial development (no fan-out)"
        )

    def test_runner_post_fanout_verification_defaults_to_false(self) -> None:
        """FanOutDevelopmentEffect.run_post_fanout_verification must default to False."""
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
            ),
        )
        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, FanOutDevelopmentEffect)
        assert effect.run_post_fanout_verification is False, (
            "run_post_fanout_verification must default to False so tests never run make verify"
        )
