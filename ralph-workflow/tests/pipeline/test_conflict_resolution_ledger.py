"""Tests for conflict resolution health integration with AgentFailureLedger.

Validates that:
- Integration resolver attempt facts route through AgentFailureLedger under PHASE_RESOLUTION.
- Per-phase isolation is strictly preserved between PHASE_RESOLUTION and other phases.
- Integration fallback works: when agent A fails 3 times, it falls over to candidate B and succeeds.
- When all candidates are unavailable / cooling down, resolution remains pending/waiting without lost rebase state.
- Cooldown and growing backoff are respected by choose_resolver / preferred_agent_index.
"""

from __future__ import annotations

from collections.abc import Sequence
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


def test_all_cooling_pending_state_preserves_rebase_state_then_resumes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When all candidates enter cooldown, pending state is preserved and execution resumes upon expiry."""
    resolved = False

    def _fake_markers(root: Path, paths: Sequence[str]) -> list[str]:
        return [] if resolved else list(paths)

    _install_seams(monkeypatch, unmerged=_CONFLICTED)
    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("agentA", "agentB")
    )

    invocations: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        invocations.append(agent_name)
        nonlocal resolved
        # First round: agentA fails, enters cooldown (consecutive_limit=1)
        # agentB is selected, agentB fails, enters cooldown
        # All candidates are cooling down -> driver waits out earliest cooldown via FakeClock
        # agentA comes off cooldown -> resumption! agentA is invoked and succeeds
        if len(invocations) < 3:
            raise RuntimeError(f"{agent_name} failed")
        resolved = True
        return True

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        ledger = AgentFailureLedger(
            clock=clock,
            consecutive_limit=1,
            base_cooldown_seconds=30.0,
        )
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
        # Both cooling, then resumption after earliest cooldown expired
        assert invocations == ["agentA", "agentB", "agentA"]
        assert session.unresolved_paths == ()
    finally:
        reset_executors(token)


def test_all_demoted_candidates_preserve_pending_rebase_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When all candidates are demoted to Spent, rebase/resolver state is preserved without infinite looping."""
    _install_seams(monkeypatch, unmerged=_CONFLICTED)
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("agentA", "agentB")
    )

    invocations: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        invocations.append(agent_name)
        raise RuntimeError(f"{agent_name} failed")

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        # total_limit=1: single failure demotes each agent permanently to Spent
        ledger = AgentFailureLedger(
            clock=clock,
            consecutive_limit=1,
            total_limit=1,
        )
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

        assert succeeded is False
        # Each agent was tried once, became Spent, and the loop terminated boundedly
        assert invocations == ["agentA", "agentB"]
        # Rebase and unresolved paths state was preserved on session
        assert session.unresolved_paths == tuple(_CONFLICTED)
        assert session.rounds_spent > 0
    finally:
        reset_executors(token)


def test_phase_isolation_through_public_integration_flow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Agent failure history in development does not contaminate resolution, and resolution failures do not leak to development."""
    resolved = False

    def _fake_markers(root: Path, paths: Sequence[str]) -> list[str]:
        return [] if resolved else list(paths)

    _install_seams(monkeypatch, unmerged=_CONFLICTED)
    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("agentA", "agentB")
    )

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)

        # 1. Agent A fails 3 times in 'development' phase -> placed in cooldown for 'development'
        for _ in range(3):
            ledger.record(
                AttemptFact(
                    phase="development",
                    agent="agentA",
                    artifact_valid=False,
                )
            )
        assert not ledger.is_available("development", "agentA")
        assert ledger.is_available(driver_module.PHASE_RESOLUTION, "agentA")

        # 2. Run public conflict resolution pipeline:
        # Despite agentA being on cooldown in 'development', agentA is completely eligible
        # in resolution phase and is selected first.
        invocations: list[str] = []

        def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
            nonlocal resolved
            del prompt_path, round_index
            invocations.append(agent_name)
            resolved = True
            return True

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
        assert invocations == ["agentA"]
        # After resolution succeeds, agentA is still on cooldown in 'development'
        assert not ledger.is_available("development", "agentA")

        # 3. In second conflict resolution run, agentA fails in resolution and falls over to agentB
        resolved = False
        session2 = ResolutionSession(ledger=ledger)
        invocations2: list[str] = []

        def _invoke_b(agent_name: str, prompt_path: Path, round_index: int) -> bool:
            nonlocal resolved
            del prompt_path, round_index
            invocations2.append(agent_name)
            if agent_name == "agentA":
                raise RuntimeError("agentA resolution failure")
            resolved = True
            return True

        succeeded2 = driver_module.run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke_b,
            clock=clock.monotonic,
            session=session2,
        )
        assert succeeded2 is True
        assert "agentB" in invocations2
        # Assert agentA remains on cooldown in development, agentB is completely eligible
        assert not ledger.is_available("development", "agentA")
        assert ledger.is_available("development", "agentB")
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


def test_changing_resolver_reaches_total_limit_instead_of_consecutive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A resolver that changes workspace files on each attempt is not demoted after 3 attempts,

    but reaches the total limit (e.g. 5) before being demoted.
    """
    _install_seams(monkeypatch, unmerged=_CONFLICTED, surviving_per_round=[[]])
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("agentA", "agentB")
    )

    from ralph.workspace import MemoryWorkspace
    from ralph.workspace.probe import DefaultWorkspaceChangeProbe

    workspace = MemoryWorkspace()
    probe = DefaultWorkspaceChangeProbe()
    invocations: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        invocations.append(agent_name)
        # Agent modifies workspace on every attempt
        workspace.write(f"work_{len(invocations)}.txt", "progress")
        if agent_name == "agentA":
            raise RuntimeError("agentA failed")
        return True

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        # consecutive_limit=3, total_limit=5
        ledger = AgentFailureLedger(
            clock=clock,
            consecutive_limit=3,
            total_limit=5,
        )
        session = ResolutionSession(
            ledger=ledger,
            workspace=workspace,
            workspace_probe=probe,
        )

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
        # Agent A ran 5 times (total limit, NOT 3 consecutive), then fell over to Agent B!
        assert invocations[:5] == ["agentA"] * 5
        assert invocations[5] == "agentB"
    finally:
        reset_executors(token)


def test_faulty_health_recording_fails_closed() -> None:
    """When ledger recording raises, fail-closed behavior raises RuntimeError."""
    from ralph.recovery.agent_failure_ledger import AttemptOutcome

    class _FaultyLedger(AgentFailureLedger):
        def record(self, fact: AttemptFact) -> AttemptOutcome:
            raise RuntimeError("simulated storage failure")

    clock = FakeClock()
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock, ledger=_FaultyLedger(clock=clock))
    )

    with pytest.raises(RuntimeError):
        bench_resolver(controller, "agentA", "error")

    with pytest.raises(RuntimeError):
        clear_resolver(controller, "agentA")


def test_fake_clock_shows_no_invocation_before_eligibility(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When candidates are on cooldown, driver waits out cooldown; if still unavailable, candidate is not invoked."""
    _install_seams(monkeypatch, unmerged=_CONFLICTED)
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("agentA",))

    invocations: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        del prompt_path, round_index
        invocations.append(agent_name)
        raise RuntimeError("always fails")

    class _FrozenClock:
        def __init__(self) -> None:
            self._time = 1000.0

        def now(self) -> float:
            return self._time

        def monotonic(self) -> float:
            return self._time

        def sleep(self, seconds: float) -> None:
            pass

        def wait_for_event(self, event: object, seconds: float) -> bool:
            return False

    frozen_clock = _FrozenClock()
    token = install_executors(clock=frozen_clock)
    try:
        ledger = AgentFailureLedger(
            clock=frozen_clock,
            consecutive_limit=1,
            base_cooldown_seconds=100.0,
        )
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
            clock=frozen_clock.monotonic,
            session=session,
        )

        assert succeeded is False
        # Agent A was invoked once, entered cooldown of 100s.
        # Since sleep didn't advance clock, agentA is still not eligible, so it was NOT invoked a second time!
        assert invocations == ["agentA"]
    finally:
        reset_executors(token)
