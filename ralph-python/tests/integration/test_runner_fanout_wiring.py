"""Integration tests for fan-out wiring in pipeline runner."""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_PLANNING
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutDevelopmentEffect, InvokeAgentEffect
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit


def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=f"Work unit {unit_id}")


def _make_policy_bundle(max_workers: int = 4) -> MagicMock:
    bundle = MagicMock()
    bundle.pipeline.phases = {
        PHASE_DEVELOPMENT: MagicMock(requires_commit=False, drain="development"),
        PHASE_PLANNING: MagicMock(requires_commit=False, drain="planning"),
    }
    bundle.pipeline.parallel_execution.max_parallel_workers = max_workers
    bundle.agents.agent_drains = {
        "development": MagicMock(chain="developer"),
        "planning": MagicMock(chain="planner"),
    }
    bundle.agents.agent_chains = {
        "developer": MagicMock(agents=["developer"]),
        "planner": MagicMock(agents=["planner"]),
    }
    return bundle


class TestFanOutRouting:
    """Test that runner routes correctly based on work_units."""

    def test_serial_when_no_work_units(self) -> None:
        """When work_units=(), development phase uses InvokeAgentEffect (serial path)."""
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=())
        policy_bundle = _make_policy_bundle()

        effect = runner_module._determine_effect_from_policy(state, policy_bundle)

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == PHASE_DEVELOPMENT

    def test_fanout_when_work_units_present(self) -> None:
        """When work_units present, development phase uses FanOutDevelopmentEffect."""
        units = (
            _make_work_unit("unit-a"),
            _make_work_unit("unit-b"),
        )
        max_workers = 3
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)
        policy_bundle = _make_policy_bundle(max_workers=max_workers)

        effect = runner_module._determine_effect_from_policy(state, policy_bundle)

        assert isinstance(effect, FanOutDevelopmentEffect)
        assert effect.work_units == units
        assert effect.max_workers == max_workers

    def test_non_development_phase_not_affected(self) -> None:
        """Other phases always use InvokeAgentEffect regardless of work_units."""
        units = (_make_work_unit("unit-a"),)
        state = PipelineState(phase=PHASE_PLANNING, work_units=units)
        policy_bundle = _make_policy_bundle()

        effect = runner_module._determine_effect_from_policy(state, policy_bundle)

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == PHASE_PLANNING
