"""Acceptance tests for the immutable per-attempt agent-failure observation.

Contract under test (RU-FACTS):

* The observation is captured ONCE per attempt at the invocation boundary
  and is immutable.
* It reaches the ordinary reducer route as an attempt-scoped payload; the
  reducer hands the fact to the single ledger owner
  (``RecoveryController.record_attempt_fact``).
* No mutable global (enum member, exception attribute) carries the fact,
  so an attempt can never inherit a previous attempt's fact and two
  attempts (other phase / other agent) never contaminate one another.

Every assertion here is on OBSERVABLE behaviour: the captured payload, the
ledger's public status, and the reducer's routing decisions.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import AgentInvocationError, InvokeOptions, invoke_agent
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.events import AgentFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from ralph.workspace.probe import WorkspaceChangeProbe
    from ralph.workspace.protocol import Workspace

_PHASE = "development"
_AGENT_A = "agent_a"
_AGENT_B = "agent_b"


class _ScriptedProbe:
    """Workspace-change probe whose measurement the test scripts.

    It records every phase it was asked to observe so the test can prove
    the baseline was taken for the attempt it belongs to.
    """

    def __init__(self, changed: bool) -> None:
        self._changed = changed
        self.observed_phases: list[str] = []
        self.measured_phases: list[str] = []

    def observe_before(self, phase: str, workspace: Workspace) -> None:
        del workspace
        self.observed_phases.append(phase)

    def has_changed(self, phase: str, workspace: Workspace) -> bool:
        del workspace
        self.measured_phases.append(phase)
        return self._changed


def _controller(clock: FakeClock | None = None) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(clock=clock or FakeClock(start=0.0))
    )


def _state(phase: str = _PHASE, agents: tuple[str, ...] = (_AGENT_A, _AGENT_B)) -> PipelineState:
    return PipelineState(
        phase=phase,
        phase_chains={
            phase: AgentChainState(agents=list(agents), current_index=0, retries=0),
        },
    )


def _capture_observation(
    tmp_path: Path,
    *,
    probe: WorkspaceChangeProbe | None,
    phase: str = _PHASE,
    agent: str = _AGENT_A,
) -> AgentFailureEvent:
    """Run the REAL invocation boundary with a fake process and capture the payload.

    The fake process replaces the subprocess launcher only; the observation
    is captured by the production boundary code around it.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("do the work\n", encoding="utf-8")
    captured: list[AgentFailureEvent] = []

    def _fake_process(cmd: list[str], ctx: object) -> object:
        del cmd, ctx
        raise AgentInvocationError(agent, 1, "fake process failure")

    options = InvokeOptions(
        workspace_probe=probe,
        workspace=MemoryWorkspace(),
        phase=phase,
        agent_name=agent,
        failure_observation_sink=captured.append,
        workspace_monitor_factory=lambda *_args, **_kwargs: None,
    )
    config = AgentConfig(cmd="fake-agent")
    with (
        pytest.MonkeyPatch.context() as patcher,
        patcher.context(),
    ):
        import ralph.agents.invoke as invoke_module

        patcher.setattr(invoke_module, "run_subprocess_and_read_lines", _fake_process)
        with pytest.raises(AgentInvocationError):
            list(invoke_agent(config, str(prompt_file), options=options))
    assert captured, "the invocation boundary produced no observation"
    return captured[0]


def test_workspace_changing_failure_is_demoted_at_total_nine_not_three(tmp_path: Path) -> None:
    """A workspace-changing failure is demoted at total=9, not at consecutive=3.

    The observation is captured at the real invocation boundary (fake probe
    + fake process), travels on the ordinary reducer route, and the ledger
    owner resets the consecutive counter because the workspace changed.
    """
    probe = _ScriptedProbe(changed=True)
    observation = _capture_observation(tmp_path, probe=probe)

    assert observation.workspace_changed is True
    assert observation.phase == _PHASE
    assert observation.agent == _AGENT_A
    assert observation.artifact_valid is False
    assert probe.observed_phases == [_PHASE]
    assert probe.measured_phases == [_PHASE]

    controller = _controller()
    state = _state()
    event = observation

    for attempt in range(1, 10):
        state, _ = reduce(state, event, None, controller)
        if attempt < 9:
            assert controller.ledger.is_available(_PHASE, _AGENT_A), (
                f"agent demoted after {attempt} workspace-changing attempts"
            )
            assert state.chain_for_phase(_PHASE).current_index == 0

    assert not controller.ledger.is_available(_PHASE, _AGENT_A)
    assert state.chain_for_phase(_PHASE).current_index == 1, (
        "the spent agent must fall over to the next agent in the chain"
    )


def test_unchanged_and_missing_observations_never_inherit_a_previous_fact(
    tmp_path: Path,
) -> None:
    """An unchanged or missing observation never inherits the previous fact.

    Three attempts that all FAIL to observe a change put the agent into
    cooldown (the consecutive counter reaches its limit). If the missing
    observation had inherited the previous attempt's "changed" fact, the
    consecutive counter would have been reset and the agent would still be
    eligible.
    """
    changed = _capture_observation(tmp_path, probe=_ScriptedProbe(changed=True))
    unchanged = _capture_observation(tmp_path, probe=_ScriptedProbe(changed=False))
    missing = _capture_observation(tmp_path, probe=None)

    assert changed.workspace_changed is True
    assert unchanged.workspace_changed is False
    assert missing.workspace_changed is None
    assert missing.resolved_workspace_changed() is False

    controller = _controller()
    state = _state()
    for observation in (changed, unchanged, missing, missing):
        state, _ = reduce(state, observation, None, controller)

    assert not controller.ledger.is_available(_PHASE, _AGENT_A), (
        "the unchanged and missing attempts were treated as changed"
    )

    # The mirror case: four attempts that each OBSERVED a change reset the
    # consecutive counter every time, so the agent stays eligible.
    changed_controller = _controller()
    changed_state = _state()
    for _attempt in range(4):
        changed_state, _ = reduce(changed_state, changed, None, changed_controller)
    assert changed_controller.ledger.is_available(_PHASE, _AGENT_A)


def test_other_phase_attempts_do_not_contaminate_one_another(tmp_path: Path) -> None:
    """Two attempts in different phases carry independent facts."""
    development = _capture_observation(
        tmp_path, probe=_ScriptedProbe(changed=True), phase=_PHASE, agent=_AGENT_A
    )
    planning = _capture_observation(
        tmp_path, probe=_ScriptedProbe(changed=False), phase="planning", agent=_AGENT_B
    )

    assert development.phase == _PHASE
    assert development.agent == _AGENT_A
    assert development.workspace_changed is True
    assert planning.phase == "planning"
    assert planning.agent == _AGENT_B
    assert planning.workspace_changed is False

    controller = _controller()
    state = _state()
    for _attempt in range(9):
        state, _ = reduce(state, development, None, controller)

    assert not controller.ledger.is_available(_PHASE, _AGENT_A)
    assert controller.ledger.is_available("planning", _AGENT_B), (
        "an attempt in another phase contaminated this agent's ledger entry"
    )


def test_bare_agent_failure_event_fails_closed_without_an_observation() -> None:
    """The bare enum carries no fact, so the attempt is recorded as unchanged."""
    controller = _controller()
    state = _state()
    state, _ = reduce(state, PipelineEvent.AGENT_FAILURE, None, controller)

    assert controller.ledger.is_available(_PHASE, _AGENT_A)
    assert state.chain_for_phase(_PHASE).current_index == 0
