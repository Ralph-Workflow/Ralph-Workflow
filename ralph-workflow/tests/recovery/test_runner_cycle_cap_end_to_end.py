"""Black-box end-to-end test: runner loops until CycleCap is exhausted.

The runner must keep looping through recovery cycles when an agent always fails,
and exit with code 1 only when the configured CycleCap is exceeded — never before.

This test uses a two-agent chain to also verify fallover behavior:
- First agent (claude) fails and exhausts budget → falls over to second agent (opencode)
- Second agent (opencode) fails and exhausts budget → chain exhausted, "failed"
- Recovery cycle completes; runner loops until CycleCap is hit
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.agents.invoke import AgentInvocationError
from ralph.config.enums import Verbosity
from ralph.pipeline import runner as runner_module
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.recovery import controller as recovery_controller_module
from ralph.recovery.connectivity import ConnectivityState
from ralph.recovery.events import FailureEvent, FalloverEvent
from ralph.recovery.testing import FakeConnectivityMonitor
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_CYCLE_CAP = 3


def _make_policy_bundle() -> PolicyBundle:
    """Build a policy bundle with a two-agent chain for fallover testing."""
    agents = AgentsPolicy(
        agent_chains={
            "dev-chain": AgentChainConfig(
                agents=["claude", "opencode"],
                max_retries=1,
                retry_delay_ms=0,
            )
        },
        agent_drains={"development": AgentDrainConfig(chain="dev-chain")},
    )
    pipeline = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


def _common_monkeypatches(
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,
    bundle: PolicyBundle,
    fake_execute: object,
    save_fn: object = None,
) -> None:
    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(workspace_root)
    )
    monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _: None)
    monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _: 0)
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _: bundle)
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(runner_module, "materialize_prepared_prompt", lambda *a, **kw: None)
    monkeypatch.setattr(runner_module.ckpt, "save", save_fn if save_fn is not None else MagicMock())
    monkeypatch.setattr(runner_module, "execute_effect_with_optional_display", fake_execute)


def test_runner_exits_via_cycle_cap_not_premature_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runner loops through recovery until CycleCap is hit, then exits with code 1.

    With a two-agent chain (claude -> opencode, each with max_retries=1)
    and CycleCap=3:
    - Cycle 1: claude fails -> budget exhausted -> fallover to opencode ->
      opencode fails -> chain exhausted -> "failed" (count=1)
    - Recovery: PreparePromptEffect -> back to development
    - Cycle 2: same sequence -> "failed" (count=2)
    - Cycle 3: same sequence -> "failed" (count=3) ->
      Cap check: count(3) >= cap(3) -> ExitFailureEffect -> runner returns 1

    Total invocations: 6 (2 agents x 3 cycles).
    """
    bundle = _make_policy_bundle()

    saved_states: list[PipelineState] = []

    def _capture_saved_state(state: PipelineState, path: object = None) -> None:
        saved_states.append(state)

    initial_state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"], current_index=0, retries=0
            )
        },
        policy_entry_phase="development",
        recovery_cycle_cap=_CYCLE_CAP,
    )

    invocation_count = 0

    def _fake_execute(*args: object, **kwargs: object) -> None:
        nonlocal invocation_count
        invocation_count += 1
        raise AgentInvocationError("claude", 1, "agent idle timeout")

    _common_monkeypatches(monkeypatch, tmp_path, bundle, _fake_execute, _capture_saved_state)

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    exit_code = runner_module.run(
        MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        _recovery_sleep=lambda _: None,
    )

    assert exit_code == 1
    # Two agents per cycle x _CYCLE_CAP cycles
    expected_invocations = 2 * _CYCLE_CAP
    assert invocation_count == expected_invocations, (
        "Expected "
        f"{expected_invocations} agent invocations "
        f"(2 agents x {_CYCLE_CAP} cycles), "
        f"got {invocation_count}"
    )


def test_runner_cycle_cap_emits_failure_events_and_fallover_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each recovery cycle records FailureEvents for both agents and a FalloverEvent.

    Captured by subscribing to the controller's bus via patching
    ralph.recovery.controller.FailureEventBus (the class used at construction time).

    With a two-agent chain:
    - Each cycle produces 2 FailureEvents (one per agent) + 1 FalloverEvent
    - Total: 2 x _CYCLE_CAP FailureEvents + _CYCLE_CAP FalloverEvents
    """

    bundle = _make_policy_bundle()

    initial_state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"], current_index=0, retries=0
            )
        },
        policy_entry_phase="development",
        recovery_cycle_cap=_CYCLE_CAP,
    )

    captured_failure_events: list[FailureEvent] = []
    captured_fallover_events: list[FalloverEvent] = []

    class _CapturingBus(recovery_controller_module.FailureEventBus):
        def __init__(self) -> None:
            super().__init__()
            self.subscribe(
                lambda evt: (
                    captured_failure_events.append(evt)
                    if isinstance(evt, FailureEvent)
                    else (
                        captured_fallover_events.append(evt)
                        if isinstance(evt, FalloverEvent)
                        else None
                    )
                )
            )

    monkeypatch.setattr(recovery_controller_module, "FailureEventBus", _CapturingBus)

    def _fake_execute(*args: object, **kwargs: object) -> None:
        raise AgentInvocationError("claude", 1, "agent idle timeout")

    _common_monkeypatches(monkeypatch, tmp_path, bundle, _fake_execute)

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    exit_code = runner_module.run(
        MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        _recovery_sleep=lambda _: None,
    )

    assert exit_code == 1
    # 2 FailureEvents per cycle (one per agent)
    expected_failure_events = 2 * _CYCLE_CAP
    assert len(captured_failure_events) == expected_failure_events, (
        "Expected "
        f"{expected_failure_events} FailureEvents "
        f"(2 agents x {_CYCLE_CAP} cycles), "
        f"got {len(captured_failure_events)}"
    )
    for evt in captured_failure_events:
        assert evt.category == "agent"
        assert evt.counted_against_budget is True
        assert evt.phase == "development"
        assert evt.agent in ("claude", "opencode")

    # 1 FalloverEvent per cycle (claude → opencode)
    assert len(captured_fallover_events) == _CYCLE_CAP, (
        f"Expected {_CYCLE_CAP} FalloverEvents (one per cycle), got {len(captured_fallover_events)}"
    )
    for evt in captured_fallover_events:
        assert evt.from_agent == "claude"
        assert evt.to_agent == "opencode"
        assert evt.phase == "development"


def test_runner_fallover_history_reflects_agent_transitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallover_history in final state shows all agent transitions."""
    bundle = _make_policy_bundle()

    initial_state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"], current_index=0, retries=0
            )
        },
        policy_entry_phase="development",
        recovery_cycle_cap=_CYCLE_CAP,
    )

    saved_states: list[PipelineState] = []

    def _capture_saved_state(state: PipelineState, path: object = None) -> None:
        saved_states.append(state)

    def _fake_execute(*args: object, **kwargs: object) -> None:
        raise AgentInvocationError("claude", 1, "agent idle timeout")

    _common_monkeypatches(monkeypatch, tmp_path, bundle, _fake_execute, _capture_saved_state)

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    exit_code = runner_module.run(
        MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        _recovery_sleep=lambda _: None,
    )

    assert exit_code == 1
    assert len(saved_states) > 0, "Expected at least one saved checkpoint"

    # Find the final saved state (last one with non-zero recovery_cycle_count)
    final_state = None
    for state in reversed(saved_states):
        if isinstance(state, PipelineState) and state.recovery_cycle_count > 0:
            final_state = state
            break

    assert final_state is not None, "No state found with recovery_cycle_count > 0"
    # _CYCLE_CAP fallover records should be present (one per recovery cycle)
    assert len(final_state.fallover_history) == _CYCLE_CAP, (
        f"Expected {_CYCLE_CAP} fallover records, got {len(final_state.fallover_history)}"
    )
    for record in final_state.fallover_history:
        assert record.from_agent == "claude"
        assert record.to_agent == "opencode"
        assert record.phase == "development"


def test_runner_recovery_cycle_count_reaches_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recovery_cycle_count in saved checkpoints reaches the cap value."""
    bundle = _make_policy_bundle()

    initial_state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"], current_index=0, retries=0
            )
        },
        policy_entry_phase="development",
        recovery_cycle_cap=_CYCLE_CAP,
    )

    saved_states: list[PipelineState] = []

    def _capture_saved_state(state: PipelineState, path: object = None) -> None:
        saved_states.append(state)

    def _fake_execute(*args: object, **kwargs: object) -> None:
        raise AgentInvocationError("claude", 1, "agent idle timeout")

    _common_monkeypatches(monkeypatch, tmp_path, bundle, _fake_execute, _capture_saved_state)

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    exit_code = runner_module.run(
        MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        _recovery_sleep=lambda _: None,
    )

    assert exit_code == 1
    cycle_counts = [s.recovery_cycle_count for s in saved_states if isinstance(s, PipelineState)]
    assert max(cycle_counts, default=0) >= _CYCLE_CAP - 1, (
        f"Expected recovery_cycle_count to reach at least {_CYCLE_CAP - 1} "
        f"in saved states; got max={max(cycle_counts, default=0)}"
    )
