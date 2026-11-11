from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
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
    _install_seams(
        monkeypatch,
        unmerged=_CONFLICTED,
        surviving_per_round=[list(_CONFLICTED)] * 3 + [[]],
    )
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
        assert invocations == ["agentA", "agentA", "agentA", "agentB"]
        assert session.recovery_controller is not None
        choice = choose_resolver(session.recovery_controller, ("agentA", "agentB"))
        assert choice.agent == "agentB"
    finally:
        reset_executors(token)


def test_all_cooling_pending_state_preserves_rebase_state_then_resumes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
        assert invocations == ["agentA", "agentB", "agentA"]
        assert session.unresolved_paths == ()
    finally:
        reset_executors(token)


def test_all_demoted_candidates_preserve_pending_rebase_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
        assert invocations == ["agentA", "agentB"]
        assert session.unresolved_paths == tuple(_CONFLICTED)
        assert session.rounds_spent > 0
    finally:
        reset_executors(token)


def test_phase_isolation_through_public_integration_flow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
        assert not ledger.is_available("development", "agentA")

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
        assert not ledger.is_available("development", "agentA")
        assert ledger.is_available("development", "agentB")
    finally:
        reset_executors(token)


def test_all_candidates_cooling_down_reports_pending_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = FakeClock()
    controller = _make_controller(clock)

    for _ in range(3):
        bench_resolver(controller, "agentA", "error A")
    for _ in range(3):
        bench_resolver(controller, "agentB", "error B")

    choice = choose_resolver(controller, ("agentA", "agentB"))
    assert choice.agent is None
    assert choice.wait_ms > 0

    clock.advance(choice.wait_ms / 1000.0)
    choice_after = choose_resolver(controller, ("agentA", "agentB"))
    assert choice_after.agent == "agentA"


def test_integration_health_does_not_contaminate_other_phases() -> None:
    clock = FakeClock()
    controller = _make_controller(clock)

    for _ in range(3):
        bench_resolver(controller, "agentA", "resolution failure")

    res_choice = choose_resolver(controller, ("agentA", "agentB"))
    assert res_choice.agent == "agentB"

    dev_choice = controller.preferred_agent_index("development", ("agentA", "agentB"))
    assert dev_choice.agent == "agentA"


def test_other_phase_failure_does_not_contaminate_resolution() -> None:
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

    dev_choice = controller.preferred_agent_index("development", ("agentA", "agentB"))
    assert dev_choice.agent == "agentB"

    res_choice = choose_resolver(controller, ("agentA", "agentB"))
    assert res_choice.agent == "agentA"


def test_clear_resolver_resets_resolution_history() -> None:
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
    _install_seams(
        monkeypatch,
        unmerged=_CONFLICTED,
        surviving_per_round=[list(_CONFLICTED)] * 5 + [[]],
    )
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
        workspace.write(f"work_{len(invocations)}.txt", "progress")
        if agent_name == "agentA":
            raise RuntimeError("agentA failed")
        return True

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
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
        assert invocations[:5] == ["agentA"] * 5
        assert invocations[5] == "agentB"
    finally:
        reset_executors(token)


def test_faulty_health_recording_fails_closed() -> None:
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

        def now_utc(self) -> datetime:
            return datetime.fromtimestamp(self._time, tz=UTC)

        def epoch(self) -> float:
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
        assert invocations == ["agentA"]
    finally:
        reset_executors(token)


def test_false_positive_resolver_success_remains_unproductive_and_demotes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
        if agent_name == "agentA":
            resolved = False
            return True
        resolved = True
        return True

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        ledger = AgentFailureLedger(clock=clock, consecutive_limit=2)
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
        assert invocations == ["agentA", "agentA", "agentB"]
        assert session.unresolved_paths == ()
    finally:
        reset_executors(token)


def test_resolver_restart_mid_streak_honors_configured_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch, unmerged=_CONFLICTED)
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("agentA", "agentB")
    )

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        ledger_1 = AgentFailureLedger(clock=clock, consecutive_limit=4)
        for _ in range(2):
            ledger_1.record(
                AttemptFact(
                    phase=driver_module.PHASE_RESOLUTION,
                    agent="agentA",
                    artifact_valid=False,
                )
            )
        snap = ledger_1.snapshot()

        fresh_clock = FakeClock(start=0.0)
        ledger_2 = AgentFailureLedger.from_snapshot(snap, clock=fresh_clock)
        assert ledger_2.consecutive_limit == 4
        assert ledger_1.is_available(driver_module.PHASE_RESOLUTION, "agentA")
        assert ledger_2.is_available(driver_module.PHASE_RESOLUTION, "agentA")

        invocations_2: list[str] = []
        resolved = False

        def _fake_markers(root: Path, paths: Sequence[str]) -> list[str]:
            return [] if resolved else list(paths)

        monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)

        def _invoke_2(agent_name: str, prompt_path: Path, round_index: int) -> bool:
            nonlocal resolved
            del prompt_path, round_index
            invocations_2.append(agent_name)
            if agent_name == "agentA":
                raise RuntimeError("agentA failed again")
            resolved = True
            return True

        session_2 = ResolutionSession(ledger=ledger_2)
        succeeded = driver_module.run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke_2,
            clock=fresh_clock.monotonic,
            session=session_2,
        )

        assert succeeded is True
        assert invocations_2 == ["agentA", "agentA", "agentB"]
    finally:
        reset_executors(token)


def test_all_cooldown_outer_integration_preserves_rebase_state_not_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.git.resolution_failure_cause import ResolutionFailureCause
    from ralph.pipeline.auto_integrate_outcome import record_conflict
    from ralph.pipeline.conflict_resolution.round_verdict import (
        ResolutionTerminationReason,
    )

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
    monkeypatch.setattr(clock, "sleep", lambda _s: None)
    token = install_executors(clock=clock)
    try:
        ledger = AgentFailureLedger(
            clock=clock,
            consecutive_limit=1,
            base_cooldown_seconds=60.0,
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
        assert invocations == ["agentA", "agentB"]
        assert session.terminal_reason == ResolutionTerminationReason.EXCEPTION
        assert session.unresolved_paths == tuple(_CONFLICTED)

        short_circuit = record_conflict(
            reason=session.exhaustion_reason,
            target="main",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )

        assert not short_circuit.is_intervention_required
        assert not short_circuit.fast_forwarded
        assert short_circuit.last_failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT

        assert session.recovery_controller is not None
        choice = choose_resolver(session.recovery_controller, ("agentA", "agentB"))
        assert choice.agent is None
        assert choice.wait_ms > 0
    finally:
        reset_executors(token)


def test_fault_activity_text_does_not_exclude_candidate() -> None:
    from ralph.pipeline.conflict_resolution.chain_candidates import skipped_candidates
    from ralph.pipeline.conflict_resolution.session import wrap_activity_listener

    session = ResolutionSession()
    listener = wrap_activity_listener(None, session, agent_name="agentA")
    assert listener is not None

    class FakeEvent:
        def __init__(self) -> None:
            self.diagnostic = {"last_activity_kind": "transport_loop_detected"}

        def __str__(self) -> str:
            return "transport_loop_detected: some event"

    event = FakeEvent()
    for _ in range(10):
        listener(event)

    assert "agentA" not in skipped_candidates(session, ("agentA", "agentB"))


def test_ledger_is_sole_authority_for_candidate_exclusion() -> None:
    from ralph.pipeline.conflict_resolution.chain_candidates import skipped_candidates
    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1)
    session = ResolutionSession(ledger=ledger)

    skipped = skipped_candidates(session, ("agentA", "agentB"))
    assert "agentA" not in skipped

    ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="agentA",
            artifact_valid=False,
        )
    )
    assert not ledger.is_available(PHASE_RESOLUTION, "agentA")

    skipped_after = skipped_candidates(session, ("agentA", "agentB"))
    assert "agentA" in skipped_after
    assert "agentB" not in skipped_after
