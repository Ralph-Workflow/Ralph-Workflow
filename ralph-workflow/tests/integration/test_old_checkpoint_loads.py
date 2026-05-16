from __future__ import annotations

import json
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import FanOutEffect, InvokeAgentEffect
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseParallelization


def _make_policy_bundle(max_workers: int = 4) -> MagicMock:
    bundle = MagicMock()
    para = PhaseParallelization(max_parallel_workers=max_workers, post_fanout_verification=False)
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


class TestOldCheckpointLoads:
    def test_old_checkpoint_missing_work_units_gets_empty_default(self) -> None:
        sample = PipelineState(phase="development").model_dump(mode="json")
        sample.pop("work_units", None)
        sample.pop("worker_states", None)

        loaded = PipelineState.model_validate_json(json.dumps(sample))
        assert loaded.work_units == ()
        assert loaded.worker_states == {}

    def test_old_checkpoint_takes_serial_path(self) -> None:
        state = PipelineState(phase="development", work_units=())
        policy_bundle = _make_policy_bundle()

        effect = determine_effect_from_policy(state, policy_bundle, config=UnifiedConfig())

        assert isinstance(effect, InvokeAgentEffect)
        assert not isinstance(effect, FanOutEffect)

    def test_fan_out_not_emitted_for_empty_work_units(self) -> None:
        state = PipelineState(phase="development", work_units=())
        policy_bundle = _make_policy_bundle()

        effect = determine_effect_from_policy(state, policy_bundle, config=UnifiedConfig())

        assert not isinstance(effect, FanOutEffect)
