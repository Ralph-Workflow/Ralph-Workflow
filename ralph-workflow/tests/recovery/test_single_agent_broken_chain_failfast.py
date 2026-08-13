"""Regression coverage for bounded broken-agent recovery on a sole-agent chain."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke import BrokenAgentExitError
from ralph.agents.timeout_clock import FakeClock
from ralph.config.loader import load_config
from ralph.pipeline.run_loop import _build_recovery_controller
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.timeout_defaults import BROKEN_AGENT_SAME_SHAPE_DEFAULT

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _minimal_policy_bundle() -> PolicyBundle:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent")


def _broken_agent_failure(controller: RecoveryController, state: PipelineState) -> PipelineState:
    next_state, effects, _ = controller.handle(
        state,
        BrokenAgentExitError("opencode", reason="no_output"),
        FailureContext(phase="development", agent="opencode"),
    )
    assert effects == []
    return next_state


def _single_agent_state() -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["opencode"], current_index=0, retries=0),
        },
    ).copy_with(last_connectivity_state="online")


def test_broken_agent_same_shape_options_default_and_override() -> None:
    """S-7: options use the bounded broken-agent default and retain overrides."""
    assert BROKEN_AGENT_SAME_SHAPE_DEFAULT == 2
    assert (
        RecoveryControllerOptions().broken_agent_same_shape_limit
        == BROKEN_AGENT_SAME_SHAPE_DEFAULT
    )
    assert RecoveryControllerOptions(broken_agent_same_shape_limit=4).broken_agent_same_shape_limit == 4


def test_broken_agent_same_shape_limit_from_general_config_reaches_runtime_builder(
    tmp_path: Path,
) -> None:
    """S-7: local [general] config controls the runtime controller bound."""
    config_file = tmp_path / "ralph-workflow.toml"
    config_file.write_text(
        "[general]\nagent_max_broken_agent_same_shape_resumes = 4\n",
        encoding="utf-8",
    )
    config = load_config(config_path=config_file)
    policy_bundle = _minimal_policy_bundle()
    controller, _ = _build_recovery_controller(_single_agent_state(), policy_bundle, config)

    state = _single_agent_state()
    for _ in range(3):
        state = _broken_agent_failure(controller, state)
        assert state.is_waiting_state is True
    state = _broken_agent_failure(controller, state)

    assert state.phase == policy_bundle.pipeline.recovery.failed_route
    assert "BROKEN_AGENT_NO_FALLOVER" in (state.last_error or "")
    assert state.is_waiting_state is False


def test_broken_agent_same_shape_bound_fails_second_consecutive_sole_agent_attempt() -> None:
    """S-7: the second identical broken sole-agent failure cannot enter cooldown."""
    policy_bundle = _minimal_policy_bundle()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            policy_bundle=policy_bundle,
        )
    )

    first_state = _broken_agent_failure(controller, _single_agent_state())
    assert first_state.phase == "development"
    assert first_state.is_waiting_state is True
    assert first_state.last_retry_delay_ms > 0

    second_state = _broken_agent_failure(controller, first_state)

    assert second_state.phase == policy_bundle.pipeline.recovery.failed_route
    assert second_state.is_waiting_state is False
    assert second_state.last_retry_delay_ms == 0
    assert "BROKEN_AGENT_NO_FALLOVER" in (second_state.last_error or "")
