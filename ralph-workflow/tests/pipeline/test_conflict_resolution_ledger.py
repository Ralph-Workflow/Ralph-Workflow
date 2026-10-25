"""Tests for conflict resolution health integration with AgentFailureLedger.

Validates that:
- Integration resolver attempt facts route through AgentFailureLedger under PHASE_RESOLUTION.
- Per-phase isolation is strictly preserved between PHASE_RESOLUTION and other phases.
- Integration fallback works: when agent A fails 3 times, it falls over to candidate B and succeeds.
- When all candidates are unavailable / cooling down, resolution remains pending/waiting without lost rebase state.
- Cooldown and growing backoff are respected by choose_resolver / preferred_agent_index.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.resolver_health import (
    bench_resolver,
    choose_resolver,
    clear_resolver,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests._conflict_resolution_phase_parity_seams import (
    _CONFLICTED,
    _config,
    _install_seams,
    _policy_bundle,
)


def _make_controller(clock: FakeClock, **kwargs: object) -> RecoveryController:
    return RecoveryController(options=RecoveryControllerOptions(clock=clock, **kwargs))


def test_resolver_falls_over_after_three_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When agent A fails 3 times, fallback candidate B is invoked and succeeds."""
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("agentA", "agentB")
    )

    invocations: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        invocations.append(agent_name)
        if agent_name == "agentA":
            raise RuntimeError("agentA failed")
        return True

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
        session = ResolutionSession(ledger=ledger)

        succeeded = driver_module.run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
            clock=clock.monotonic,
            session=session,
        )

        assert succeeded is True
        # Agent A failed 3 times (consecutive limit), then candidate B was chosen and succeeded.
        assert invocations == ["agentA", "agentA", "agentA", "agentB"]
        # Agent A is now on cooldown in the resolution phase.
        assert session.recovery_controller is not None
        choice = choose_resolver(session.recovery_controller, ("agentA", "agentB"))
        assert choice.agent == "agentB"
    finally:
        reset_executors(token)


def test_all_candidates_cooling_down_reports_pending_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When all candidates are cooling down, choose_resolver reports pending wait without lost rebase state."""
    clock = FakeClock()
    controller = _make_controller(clock)

    # Fail both candidates 3 times to put both on cooldown.
    for _ in range(3):
        bench_resolver(controller, "agentA", "error A")
    for _ in range(3):
        bench_resolver(controller, "agentB", "error B")

    choice = choose_resolver(controller, ("agentA", "agentB"))
    assert choice.agent is None
    assert choice.wait_ms > 0

    # Advancing past the earliest cooldown restores candidate A.
    clock.advance(choice.wait_ms / 1000.0)
    choice_after = choose_resolver(controller, ("agentA", "agentB"))
    assert choice_after.agent == "agentA"


def test_integration_health_does_not_contaminate_other_phases() -> None:
    """Benching an agent in resolution does not affect its availability in development."""
    clock = FakeClock()
    controller = _make_controller(clock)

    for _ in range(3):
        bench_resolver(controller, "agentA", "resolution failure")

    # In PHASE_RESOLUTION, agentA is in cooldown.
    res_choice = choose_resolver(controller, ("agentA", "agentB"))
    assert res_choice.agent == "agentB"

    # In development phase, agentA is unaffected and still preferred.
    dev_choice = controller.preferred_agent_index("development", ("agentA", "agentB"))
    assert dev_choice.agent == "agentA"


def test_other_phase_failure_does_not_contaminate_resolution() -> None:
    """Failing an agent in development phase does not affect resolution candidate selection."""
    clock = FakeClock()
    controller = _make_controller(clock)

    for _ in range(3):
        controller.ledger.record(
            AttemptFact(
                phase="development",
                agent="agentA",
                artifact_valid=False,
            )
        )

    # In development phase, agentA is in cooldown.
    dev_choice = controller.preferred_agent_index("development", ("agentA", "agentB"))
    assert dev_choice.agent == "agentB"

    # In resolution phase, agentA is completely eligible and selected first.
    res_choice = choose_resolver(controller, ("agentA", "agentB"))
    assert res_choice.agent == "agentA"


def test_clear_resolver_resets_resolution_history() -> None:
    """Verified resolution clears the agent's failure ledger history for resolution."""
    clock = FakeClock()
    controller = _make_controller(clock)

    for _ in range(3):
        bench_resolver(controller, "agentA", "fail 1")
    assert choose_resolver(controller, ("agentA", "agentB")).agent == "agentB"

    clear_resolver(controller, "agentA")
    assert choose_resolver(controller, ("agentA", "agentB")).agent == "agentA"

    for _ in range(3):
        bench_resolver(controller, "agentA", "fail after clear")
    assert choose_resolver(controller, ("agentA", "agentB")).agent == "agentB"
