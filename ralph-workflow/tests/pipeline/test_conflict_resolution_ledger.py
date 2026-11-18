from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.resolver_health import (
    choose_resolver,
    clear_resolver,
    record_resolver_failure,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact, Cooldown
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests._conflict_resolution_phase_parity_seams import (
    _CONFLICTED,
    _config,
    _install_seams,
    _policy_bundle,
)


def _make_controller(clock: FakeClock, **kwargs: object) -> RecoveryController:
    return RecoveryController(options=RecoveryControllerOptions(clock=clock, **kwargs))


def _session_with_controller(
    clock: object,
    ledger: AgentFailureLedger,
    **kwargs: object,
) -> ResolutionSession:
    controller = RecoveryController(ledger=ledger, options=RecoveryControllerOptions(clock=clock))
    kwargs.setdefault("ledger", ledger)
    kwargs.setdefault("recovery_controller", controller)
    return ResolutionSession(**kwargs)


def test_resolver_falls_over_after_three_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(
        monkeypatch,
        unmerged=_CONFLICTED,
        surviving_per_round=[list(_CONFLICTED), []],
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
        session = _session_with_controller(clock, ledger)

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
        assert invocations == ["agentA", "agentB"]
        assert session.recovery_controller is not None
        choice = choose_resolver(session.recovery_controller, ("agentA", "agentB"))
        assert choice.agent == "agentA"
    finally:
        reset_executors(token)


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_phase_isolation_through_public_integration_flow": (
        "operator rule: an agent is either on cooldown or not; the cooldown is keyed by agent "
        "only, so a development unproductive_limit cooldown keeps the agent out of resolution "
        "until it expires, after which it resolves again, and the test asserts both"
    ),
    "test_integration_health_does_not_contaminate_other_phases": (
        "operator rule: an agent is either on cooldown or not; replaced by "
        "test_resolution_cooldown_applies_to_other_phases_until_it_expires because the "
        "cooldown is keyed by agent only and applies in every phase until it expires"
    ),
    "test_other_phase_failure_does_not_contaminate_resolution": (
        "operator rule: an agent is either on cooldown or not; replaced by "
        "test_other_phase_cooldown_applies_to_resolution_until_it_expires because the "
        "cooldown is keyed by agent only and applies in every phase until it expires"
    ),
    "test_resolution_cooldown_applies_to_other_phases_until_it_expires": (
        "operator rule: an agent is either on cooldown or not; asserts the agent-keyed "
        "cooldown and its expiry"
    ),
    "test_other_phase_cooldown_applies_to_resolution_until_it_expires": (
        "operator rule: an agent is either on cooldown or not; asserts the agent-keyed "
        "cooldown and its expiry"
    ),
    "test_fault_activity_text_does_not_exclude_candidate": (
        "operator rule: an agent is either on cooldown or not; skipped_candidates is gone, "
        "so the test asserts the candidate stays eligible in the session ledger"
    ),
    "test_ledger_is_sole_authority_for_candidate_exclusion": (
        "operator rule: an agent is either on cooldown or not; skipped_candidates is gone, "
        "so the test asserts the ledger starts an expiring unproductive_limit cooldown and "
        "the candidate is available again after it expires"
    ),
    "test_resolver_falls_over_after_three_failures": (
        "the shared controller falls over to the next eligible candidate in the same round "
        "after a failed attempt instead of retrying the failed resolver three times, and one "
        "ambiguous failure leaves the primary resolver preferred for the next stop"
    ),
    "test_all_cooling_pending_state_preserves_rebase_state_then_resumes": (
        "a stop whose every resolver is cooling down ends pending; the resume is a "
        "second resolution call after the cooldown instead of a wait inside the first"
    ),
    "test_changing_resolver_reaches_total_limit_instead_of_consecutive": (
        "each failed attempt falls over to the fallback candidate in that round, "
        "so the five attempts that reach the total limit interleave with agentB"
    ),
    "test_resolver_restart_mid_streak_honors_configured_limit": (
        "each failed attempt falls over to the fallback candidate in that round, "
        "so the remaining two failures that reach consecutive limit interleave with agentB"
    ),
    "test_false_positive_resolver_success_remains_unproductive_and_demotes": (
        "a false-positive success cools its agent down before it is invoked again, "
        "so the two charged attempts that demote it interleave with the next candidate"
    ),
}


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
        session = _session_with_controller(clock, ledger)

        def _resolve() -> bool:
            return driver_module.run_conflict_resolution_pipeline(
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

        assert _resolve() is False
        assert invocations == ["agentA", "agentB"]
        assert session.unresolved_paths == tuple(_CONFLICTED)

        clock.advance(30.0)

        assert _resolve() is True
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
        session = _session_with_controller(clock, ledger)

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
        assert not ledger.is_available(driver_module.PHASE_RESOLUTION, "agentA")
        cooldown_wait_ms = ledger.earliest_wait_ms(driver_module.PHASE_RESOLUTION, ["agentA"])
        assert cooldown_wait_ms > 0

        invocations: list[str] = []

        def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
            nonlocal resolved
            del prompt_path, round_index
            invocations.append(agent_name)
            resolved = True
            return True

        session = _session_with_controller(clock, ledger)
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
        assert invocations == ["agentB"]
        assert not ledger.is_available("development", "agentA")

        clock.advance(cooldown_wait_ms / 1000.0)
        assert ledger.is_available("development", "agentA")
        assert ledger.is_available(driver_module.PHASE_RESOLUTION, "agentA")

        resolved = False
        session2 = _session_with_controller(clock, ledger)
        invocations2: list[str] = []

        def _invoke_b(agent_name: str, prompt_path: Path, round_index: int) -> bool:
            nonlocal resolved
            del prompt_path, round_index
            invocations2.append(agent_name)
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
        assert invocations2 == ["agentA"]
        assert ledger.is_available("development", "agentA")
        assert ledger.is_available("development", "agentB")
    finally:
        reset_executors(token)


def test_all_candidates_cooling_down_reports_pending_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = FakeClock()
    controller = _make_controller(clock)

    for _ in range(3):
        record_resolver_failure(controller, "agentA", "error A")
    for _ in range(3):
        record_resolver_failure(controller, "agentB", "error B")

    choice = choose_resolver(controller, ("agentA", "agentB"))
    assert choice.agent is None
    assert choice.wait_ms > 0

    clock.advance(choice.wait_ms / 1000.0)
    choice_after = choose_resolver(controller, ("agentA", "agentB"))
    assert choice_after.agent == "agentA"


def test_resolution_cooldown_applies_to_other_phases_until_it_expires() -> None:
    clock = FakeClock()
    controller = _make_controller(clock)

    for _ in range(3):
        record_resolver_failure(controller, "agentA", "resolution failure")

    assert choose_resolver(controller, ("agentA", "agentB")).agent == "agentB"
    assert controller.preferred_agent_index("development", ("agentA", "agentB")).agent == "agentB"
    wait_ms = choose_resolver(controller, ("agentA",)).wait_ms
    assert wait_ms > 0

    clock.advance(wait_ms / 1000.0)

    assert choose_resolver(controller, ("agentA", "agentB")).agent == "agentA"
    assert controller.preferred_agent_index("development", ("agentA", "agentB")).agent == "agentA"


def test_other_phase_cooldown_applies_to_resolution_until_it_expires() -> None:
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

    status = controller.ledger.status("development", "agentA")
    assert isinstance(status, Cooldown)
    assert status.reason == "unproductive_limit"
    assert controller.preferred_agent_index("development", ("agentA", "agentB")).agent == "agentB"
    assert choose_resolver(controller, ("agentA", "agentB")).agent == "agentB"
    wait_ms = choose_resolver(controller, ("agentA",)).wait_ms
    assert wait_ms > 0

    clock.advance(wait_ms / 1000.0)

    assert choose_resolver(controller, ("agentA", "agentB")).agent == "agentA"


def test_clear_resolver_resets_resolution_history() -> None:
    clock = FakeClock()
    controller = _make_controller(clock)

    for _ in range(3):
        record_resolver_failure(controller, "agentA", "fail 1")
    assert choose_resolver(controller, ("agentA", "agentB")).agent == "agentB"

    clear_resolver(controller, "agentA")
    assert choose_resolver(controller, ("agentA", "agentB")).agent == "agentA"

    for _ in range(3):
        record_resolver_failure(controller, "agentA", "fail after clear")
    assert choose_resolver(controller, ("agentA", "agentB")).agent == "agentB"


def test_changing_resolver_reaches_total_limit_instead_of_consecutive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(
        monkeypatch,
        unmerged=_CONFLICTED,
        surviving_per_round=[list(_CONFLICTED)] * 9 + [[]],
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
        session = _session_with_controller(
            clock,
            ledger,
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
        assert invocations == ["agentA", "agentB"] * 5
        assert not ledger.is_available(driver_module.PHASE_RESOLUTION, "agentA")
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
        record_resolver_failure(controller, "agentA", "error")

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
        session = _session_with_controller(frozen_clock, ledger)

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
        resolved = invocations.count("agentB") == 2
        return True

    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        ledger = AgentFailureLedger(clock=clock, consecutive_limit=2)
        session = _session_with_controller(clock, ledger)

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
        assert invocations == ["agentA", "agentB", "agentA", "agentB"]
        assert not ledger.is_available(driver_module.PHASE_RESOLUTION, "agentA")
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
        agent_b_attempts = 0

        def _fake_markers(root: Path, paths: Sequence[str]) -> list[str]:
            return [] if agent_b_attempts >= 2 else list(paths)

        monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)

        def _invoke_2(agent_name: str, prompt_path: Path, round_index: int) -> bool:
            nonlocal agent_b_attempts
            del prompt_path, round_index
            invocations_2.append(agent_name)
            if agent_name == "agentA":
                raise RuntimeError("agentA failed again")
            agent_b_attempts += 1
            return True

        session_2 = _session_with_controller(fresh_clock, ledger_2)
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
        assert invocations_2 == ["agentA", "agentB", "agentA", "agentB"]
        assert not ledger_2.is_available(driver_module.PHASE_RESOLUTION, "agentA")
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
        session = _session_with_controller(clock, ledger)

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
    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
    from ralph.pipeline.conflict_resolution.session import wrap_activity_listener

    ledger = AgentFailureLedger(clock=FakeClock())
    session = ResolutionSession(ledger=ledger)
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

    assert ledger.is_available(PHASE_RESOLUTION, "agentA")
    assert ledger.status(PHASE_RESOLUTION, "agentA").is_eligible


def test_ledger_is_sole_authority_for_candidate_exclusion() -> None:
    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1)

    assert ledger.is_available(PHASE_RESOLUTION, "agentA")

    ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="agentA",
            artifact_valid=False,
        )
    )

    status = ledger.status(PHASE_RESOLUTION, "agentA")
    assert isinstance(status, Cooldown)
    assert status.reason == "unproductive_limit"
    assert not ledger.is_available(PHASE_RESOLUTION, "agentA")
    assert ledger.is_available(PHASE_RESOLUTION, "agentB")
    wait_ms = ledger.earliest_wait_ms(PHASE_RESOLUTION, ["agentA"])
    assert wait_ms > 0

    clock.advance(wait_ms / 1000.0)

    assert ledger.is_available(PHASE_RESOLUTION, "agentA")
