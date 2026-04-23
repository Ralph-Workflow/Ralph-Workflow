"""Black-box end-to-end test: runner loops until CycleCap is exhausted.

The runner must keep looping through recovery cycles when an agent always fails,
and exit with code 1 only when the configured CycleCap is exceeded — never before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from ralph.agents.invoke import AgentInactivityTimeoutError
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
from ralph.recovery.connectivity import ConnectivityState
from ralph.recovery.events import FailureEvent, FalloverEvent
from ralph.recovery.testing import FakeConnectivityMonitor
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_CYCLE_CAP = 2


def _make_policy_bundle() -> PolicyBundle:
    agents = AgentsPolicy(
        agent_chains={
            "dev-chain": AgentChainConfig(
                agents=["claude"],
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
    tmp_git_repo: Path,
    bundle: PolicyBundle,
    fake_execute: Any,
    save_fn: Any = None,
) -> None:
    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_git_repo)
    )
    monkeypatch.setattr(runner_module, "_write_start_commit_if_absent", lambda _: None)
    monkeypatch.setattr(runner_module, "_validate_custom_mcp_servers", lambda _: 0)
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _: bundle)
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(
        runner_module, "_materialize_agent_prompt_if_needed", lambda *a, **kw: None
    )
    monkeypatch.setattr(runner_module, "_materialize_prepared_prompt", lambda *a, **kw: None)
    monkeypatch.setattr(runner_module.ckpt, "save", save_fn if save_fn is not None else MagicMock())
    monkeypatch.setattr(runner_module, "_execute_effect_with_optional_display", fake_execute)


def test_runner_exits_via_cycle_cap_not_premature_termination(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runner loops through recovery until CycleCap is hit, then exits with code 1.

    With a single-agent chain (max_retries=1) and CycleCap=2:
    - Cycle 1: agent fails → budget exhausted → chain exhausted → PHASE_FAILED (count=1)
    - Recovery: PreparePromptEffect → back to development
    - Cycle 2: agent fails again → chain exhausted → PHASE_FAILED (count=2)
    - Cap check: count(2) >= cap(2) → ExitFailureEffect → runner returns 1
    """
    bundle = _make_policy_bundle()

    initial_state = PipelineState(
        phase="development",
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
        policy_entry_phase="development",
        recovery_cycle_cap=_CYCLE_CAP,
    )

    invocation_count = 0
    saved_states: list[PipelineState] = []

    def _fake_execute(*args: Any, **kwargs: Any) -> None:
        nonlocal invocation_count
        invocation_count += 1
        raise AgentInactivityTimeoutError("claude", 30.0)

    _common_monkeypatches(monkeypatch, tmp_git_repo, bundle, _fake_execute, saved_states.append)

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    exit_code = runner_module.run(
        MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        _recovery_sleep=lambda _: None,
    )

    assert exit_code == 1
    assert invocation_count == _CYCLE_CAP, (
        f"Expected {_CYCLE_CAP} agent invocations (one per cycle), got {invocation_count}"
    )


def test_runner_cycle_cap_emits_failure_events_per_cycle(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each recovery cycle records a FailureEvent on the internal event bus.

    Captured by subscribing to the controller's bus via patching
    ralph.recovery.controller.FailureEventBus (the class used at construction time).
    """
    from ralph.recovery import controller as recovery_controller_module  # noqa: PLC0415

    bundle = _make_policy_bundle()

    initial_state = PipelineState(
        phase="development",
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
        policy_entry_phase="development",
        recovery_cycle_cap=_CYCLE_CAP,
    )

    captured_failure_events: list[FailureEvent] = []
    captured_fallover_events: list[FalloverEvent] = []

    class _CapturingBus(recovery_controller_module.FailureEventBus):  # type: ignore[misc]
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

    def _fake_execute(*args: Any, **kwargs: Any) -> None:
        raise AgentInactivityTimeoutError("claude", 30.0)

    _common_monkeypatches(monkeypatch, tmp_git_repo, bundle, _fake_execute)

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    exit_code = runner_module.run(
        MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        _recovery_sleep=lambda _: None,
    )

    assert exit_code == 1
    assert len(captured_failure_events) == _CYCLE_CAP, (
        f"Expected {_CYCLE_CAP} FailureEvents (one per cycle), "
        f"got {len(captured_failure_events)}"
    )
    for evt in captured_failure_events:
        assert evt.category == "agent"
        assert evt.counted_against_budget is True
        assert evt.phase == "development"
        assert evt.agent == "claude"


def test_runner_cycle_cap_recovery_count_matches_cap(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recovery_cycle_count in saved checkpoints reaches the cap value."""
    bundle = _make_policy_bundle()

    initial_state = PipelineState(
        phase="development",
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
        policy_entry_phase="development",
        recovery_cycle_cap=_CYCLE_CAP,
    )

    saved_states: list[PipelineState] = []

    def _fake_execute(*args: Any, **kwargs: Any) -> None:
        raise AgentInactivityTimeoutError("claude", 30.0)

    _common_monkeypatches(monkeypatch, tmp_git_repo, bundle, _fake_execute, saved_states.append)

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    exit_code = runner_module.run(
        MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        _recovery_sleep=lambda _: None,
    )

    assert exit_code == 1
    cycle_counts = [
        s.recovery_cycle_count
        for s in saved_states
        if isinstance(s, PipelineState)
    ]
    assert max(cycle_counts, default=0) >= _CYCLE_CAP - 1, (
        f"Expected recovery_cycle_count to reach at least {_CYCLE_CAP - 1} "
        f"in saved states; got max={max(cycle_counts, default=0)}"
    )
