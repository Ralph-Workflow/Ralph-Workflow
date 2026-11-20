from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline import resolve_conflicted_rebase
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.round_verdict import round_termination_reason
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.conflict_resolution.status import ResolutionStatusReporter
from ralph.pipeline.resolution_outcome import ResolvedOutcome, RetryLaterOutcome
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from tests._conflict_resolution_futile_loop_support import (
    _CONFIG,
    _CONFLICTED,
    _advance_past,
    _cooling,
    _install_seams,
    _policy_bundle,
)

pytestmark = pytest.mark.timeout_seconds(5)

_REPEATED_WATCHDOG_TICKS = 6

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_all_cooling_candidates_wait_on_cooldown_then_report_no_runnable_candidate (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_all_spent_candidates_wait_on_cooldown_then_report_candidate_exhausted with an unchanged body"
    ),
    "test_ralph_infrastructure_faults_are_not_recorded_as_a_chain_without_a_runnable_resolver": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_ralph_infrastructure_faults_are_not_recorded_as_an_exhausted_chain with an unchanged body"
    ),
    "test_a_real_no_runnable_resolver_stop_is_still_recorded": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_a_real_resolver_exhaustion_is_still_recorded with an unchanged body"
    ),
    "test_a_clean_stop_is_not_reported_as_a_failure_reason": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown')"
    ),
    "test_every_reason_less_exit_now_names_itself": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown')"
    ),
    "test_the_durable_evidence_does_not_assert_a_scan_that_never_ran": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown')"
    ),
    "test_dead_tool_surface_fails_fast_and_hands_over": (
        "operator rule: agent availability is cooldown-only; the resolver no longer writes "
        "cooldown state directly, so the candidate cooldown is charged through the shared "
        "recovery ledger as an unproductive_limit cooldown"
    ),
    "test_known_dead_surface_is_not_reentered": (
        "operator rule: an agent is either on cooldown or not; skipped_candidates is gone, so "
        "the test asserts the unproductive_limit cooldown and its expiry"
    ),
    "test_a_resolver_quoting_ralphs_own_fault_tokens_survives": (
        "operator rule: an agent is either on cooldown or not; skipped_candidates is gone, so "
        "the test asserts the agent is not cooling"
    ),
    "test_a_watchdog_authored_fault_never_bars_the_surface": (
        "the resolver no longer classifies activity text; the watchdog's fault marker is "
        "activity like any other and never decides the resolver's verdict"
    ),
    "test_transport_loop_detected_is_a_typed_attempt_failure": (
        "removed: the resolver's message-text classifier was deleted with the shared agent "
        "path, so no text can produce the transport-loop verdict"
    ),
    "test_supervision_relay_error_is_a_typed_attempt_failure": (
        "removed: the resolver's message-text classifier was deleted with the shared agent "
        "path; a supervision fault is reported from the typed exception instead"
    ),
    "test_agent_prose_about_tool_surfaces_is_not_a_ralph_fault": (
        "removed: the resolver's message-text classifier was deleted with the shared agent "
        "path, so agent prose is never inspected"
    ),
    "test_real_ralph_fault_tokens_are_still_classified": (
        "removed: the resolver's message-text classifier was deleted with the shared agent "
        "path, so fault tokens in agent text are no longer classified"
    ),
    "test_status_reports_activity_whatever_the_activity_text_says": (
        "the status reporter no longer suppresses a report because the activity text looks "
        "like a fault; the report is emitted for the activity either way"
    ),
    "test_an_agents_activity_event_cannot_kill_its_own_tool_surface": (
        "operator rule: an agent is either on cooldown or not; skipped_candidates is gone, so "
        "the test asserts the agent is not cooling"
    ),
    "test_an_infrastructure_fault_does_not_kill_the_agent_for_the_whole_rebase": (
        "operator rule: an agent is either on cooldown or not; skipped_candidates is gone, so "
        "the test asserts the agent is not cooling"
    ),
    "test_a_chain_barred_only_by_ralphs_own_faults_is_retried_not_skipped": (
        "operator rule: an agent is either on cooldown or not; skipped_candidates is gone, so "
        "the test asserts no candidate is cooling"
    ),
    "test_an_unspendable_chain_reports_candidate_exhausted": (
        "operator rule: an agent is either on cooldown or not; a chain at its total limit is "
        "cooling down, so the run reports CANDIDATE_UNAVAILABLE and invokes the primary again "
        "once the fake clock passes the expiry"
    ),
    "test_an_unspendable_chain_reports_candidate_unavailable_until_its_cooldowns_expire": (
        "operator rule: an agent is either on cooldown or not; replaces the permanent "
        "exhaustion test with the expiring cooldown contract"
    ),
    "test_all_spent_candidates_yields_candidate_exhausted_terminal_reason": (
        "operator rule: an agent is either on cooldown or not; the total limit starts a 60 s "
        "unproductive_limit cooldown instead of Spent, so the chain reports "
        "CANDIDATE_UNAVAILABLE and only reports NO_RUNNABLE_CANDIDATE after the expiry"
    ),
    "test_all_cooling_candidates_wait_on_cooldown_then_report_no_runnable_candidate": (
        "operator rule: an agent is either on cooldown or not; replaces the Spent exhaustion "
        "test with the expiring cooldown contract"
    ),
    "test_a_candidate_the_registry_cannot_produce_stays_barred_for_the_run": (
        "operator rule: an agent is either on cooldown or not; a registry miss starts a "
        "candidate_unavailable cooldown instead of a per-run bar, so the candidate is skipped "
        "while cooling and invoked again after the expiry"
    ),
    "test_a_candidate_the_registry_cannot_produce_cools_down_until_its_expiry": (
        "operator rule: an agent is either on cooldown or not; replaces the per-run bar test "
        "with the expiring cooldown contract"
    ),
    "test_a_new_stop_starts_with_a_chargeable_budget (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
    "test_a_failed_mechanical_attempt_still_reaches_the_resolver (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
    "test_an_infrastructure_fault_does_not_kill_the_agent_for_the_whole_rebase (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
    "test_a_chain_barred_only_by_ralphs_own_faults_is_retried_not_skipped (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
    "test_a_candidate_the_registry_cannot_produce_cools_down_until_its_expiry (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
    "test_every_reason_less_exit_now_names_itself (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
    "test_the_operator_headline_names_what_the_resolver_reported (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
    "test_a_binary_conflict_does_not_starve_the_text_conflicts_beside_it (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
    "test_a_submodule_pointer_beside_a_text_conflict_still_spends_the_chain (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
    "test_the_durable_evidence_does_not_assert_a_scan_that_never_ran (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
    "test_a_clean_stop_is_not_reported_as_a_failure_reason (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
    "test_mechanical_staging_never_masks_a_path_nobody_can_repair (moved)": (
        "the check moved unchanged to tests/test_conflict_resolution_stop_evidence.py when "
        "this module was split to stay under the 1000-line repo-structure limit"
    ),
}


def test_dead_tool_surface_fails_fast_and_hands_over(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch)
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock, ledger=ledger))
    session = ResolutionSession(
        ledger=controller.ledger,
        recovery_controller=controller,
    )
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        if agent_name == "primary":
            session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
            session.charge_conflict_budget = False
            return False
        return False

    assert not isinstance(
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=UnifiedConfig.model_validate({"general": {}}),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
            session=session,
        ),
        ResolvedOutcome,
    )
    assert called[0] == "primary"
    assert "fallback" in called
    assert called.count("primary") == 1
    primary = _cooling(session, ("primary", "fallback"))["primary"]
    assert primary.reason == "unproductive_limit"
    assert session.terminal_reason is ResolutionTerminationReason.ATTEMPT_FAILED
    assert session.charge_conflict_budget is True
    _advance_past(clock, primary)
    assert "primary" not in _cooling(session, ("primary", "fallback"))


def test_known_dead_surface_is_not_reentered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch)
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="primary",
            artifact_valid=False,
        )
    )
    session = ResolutionSession(
        ledger=ledger,
        recovery_controller=RecoveryController(
            ledger=ledger, options=RecoveryControllerOptions(clock=clock)
        ),
    )
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        return False

    run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=_CONFIG,
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=_invoke,
        session=session,
    )
    assert "primary" not in called
    assert set(called) == {"fallback"}
    primary = _cooling(session, ("primary", "fallback"))["primary"]
    assert primary.reason == "unproductive_limit"
    _advance_past(clock, primary)
    assert "primary" not in _cooling(session, ("primary", "fallback"))


def test_status_reports_activity_whatever_the_activity_text_says() -> None:
    reporter = ResolutionStatusReporter(
        display=None,
        target="main",
        round_index=1,
        round_cap=3,
        stop_index=None,
        stop_cap=None,
        clock=lambda: 1.0,
        interval_seconds=0.0,
        started_at=0.0,
        unresolved_paths=tuple(_CONFLICTED),
        agent_name="primary",
    )

    event = type(
        "_Event",
        (),
        {
            "diagnostic": {
                "last_activity_kind": "transport_loop_detected",
                "last_activity_age_seconds": 0.56,
            }
        },
    )()
    reporter.observe(event)
    assert reporter._last_emitted_at == 1.0


def test_a_resolver_quoting_ralphs_own_fault_tokens_survives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.agents.idle_watchdog.waiting_status_event import WaitingStatusEvent
    from ralph.agents.idle_watchdog.waiting_status_kind import WaitingStatusKind
    from ralph.pipeline.conflict_resolution.session import wrap_activity_listener

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock)
    session = ResolutionSession(ledger=ledger)
    listener = wrap_activity_listener(None, session)
    assert listener is not None
    for echoed in (
        'read attempt_fault.py: _TRANSPORT_LOOP = "transport_loop_detected"',
        "grep: transport_loop_detected (3 matches)",
        "edit: SUPERVISION_INFRASTRUCTURE_FAILURE: activity relay sender: {exc}",
    ):
        listener(
            WaitingStatusEvent(
                kind=next(iter(WaitingStatusKind)),
                cumulative_seconds=1.0,
                current_run_seconds=1.0,
                idle_elapsed_seconds=1.0,
                ceiling_seconds=900.0,
                suspect_threshold_seconds=None,
                diagnostic={"last_activity_kind": "mcp_tool", "current_subagent_tool_call": echoed},
                subagent_activity=echoed,
                stall_active=False,
            )
        )
    assert "claude" not in _cooling(session, ("claude",))
    assert session.terminal_reason is None
    assert ledger.is_available(PHASE_RESOLUTION, "claude") is True


def test_a_watchdog_authored_fault_never_bars_the_surface() -> None:
    from ralph.agents.idle_watchdog.waiting_status_event import WaitingStatusEvent
    from ralph.agents.idle_watchdog.waiting_status_kind import WaitingStatusKind
    from ralph.pipeline.conflict_resolution.session import wrap_activity_listener

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock)
    session = ResolutionSession(ledger=ledger)
    listener = wrap_activity_listener(None, session)
    assert listener is not None
    faulting = WaitingStatusEvent(
        kind=next(iter(WaitingStatusKind)),
        cumulative_seconds=1.0,
        current_run_seconds=1.0,
        idle_elapsed_seconds=1.0,
        ceiling_seconds=900.0,
        suspect_threshold_seconds=None,
        diagnostic={"last_activity_kind": "transport_loop_detected"},
        subagent_activity="working",
        stall_active=False,
    )
    listener(faulting)
    assert "claude" not in _cooling(session, ("claude",)), "one tick is not a barred agent"
    assert session.last_attempt_saw_activity is True, "a fault tick is still activity"

    for _ in range(_REPEATED_WATCHDOG_TICKS):
        listener(faulting)
    assert "claude" not in _cooling(session, ("claude",)), "a plumbing fault is never run-scoped"
    assert session.terminal_reason is None, "activity text never decides the resolver's verdict"
    assert ledger.is_available(PHASE_RESOLUTION, "claude") is True


def test_an_unspendable_chain_reports_candidate_unavailable_until_its_cooldowns_expire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch)
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    for agent in ("primary", "fallback"):
        ledger.record(
            AttemptFact(
                phase=PHASE_RESOLUTION,
                agent=agent,
                artifact_valid=False,
            )
        )
    session = ResolutionSession(
        ledger=ledger,
        recovery_controller=RecoveryController(
            ledger=ledger, options=RecoveryControllerOptions(clock=clock)
        ),
    )
    cooling = _cooling(session, ("primary", "fallback"))
    assert {name: status.reason for name, status in cooling.items()} == {
        "primary": "unproductive_limit",
        "fallback": "unproductive_limit",
    }
    called: list[str] = []
    outcome = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=_CONFIG,
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda name, _p, _r: called.append(name) is None,
        session=session,
    )
    assert isinstance(outcome, RetryLaterOutcome)
    assert called == []
    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE

    _advance_past(clock, max(cooling.values(), key=lambda status: status.until_ms))
    assert _cooling(session, ("primary", "fallback")) == {}
    later = ResolutionSession(ledger=ledger, recovery_controller=session.recovery_controller)
    driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=_CONFIG,
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda name, _p, _r: called.append(name) is None,
        session=later,
    )
    assert called[0] == "primary"


def test_all_cooling_candidates_wait_on_cooldown_then_report_no_runnable_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_seams(monkeypatch)
    clock = FakeClock(start=100.0)
    candidates = ("primary", "fallback")

    spent_ledger = AgentFailureLedger(clock=clock, total_limit=1)
    for agent in candidates:
        spent_ledger.record(
            AttemptFact(
                phase=PHASE_RESOLUTION,
                agent=agent,
                artifact_valid=False,
            )
        )
    spent_wait_ms = spent_ledger.earliest_wait_ms(PHASE_RESOLUTION, candidates)
    assert spent_wait_ms == 60_000

    session_spent = ResolutionSession(
        ledger=spent_ledger,
        recovery_controller=RecoveryController(
            ledger=spent_ledger, options=RecoveryControllerOptions(clock=clock)
        ),
    )
    assert (
        round_termination_reason(session_spent, invoked=False, candidates=candidates)
        is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    )
    _ = driver_module.run_conflict_resolution_outcome(
        root=tmp_path,
        target="main",
        config=_CONFIG,
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=lambda *_args: True,
        session=session_spent,
    )
    assert session_spent.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE

    clock.advance(spent_wait_ms / 1000.0)
    assert spent_ledger.earliest_wait_ms(PHASE_RESOLUTION, candidates) == 0
    expired = ResolutionSession(
        ledger=spent_ledger, recovery_controller=session_spent.recovery_controller
    )
    assert (
        round_termination_reason(expired, invoked=False, candidates=candidates)
        is ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE
    )

    cooldown_ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=5)
    for agent in candidates:
        cooldown_ledger.record(
            AttemptFact(
                phase=PHASE_RESOLUTION,
                agent=agent,
                artifact_valid=False,
            )
        )
    wait_ms = cooldown_ledger.earliest_wait_ms(PHASE_RESOLUTION, candidates)
    assert wait_ms > 0

    session_cooldown = ResolutionSession(
        ledger=cooldown_ledger,
        recovery_controller=RecoveryController(
            ledger=cooldown_ledger, options=RecoveryControllerOptions(clock=clock)
        ),
    )
    assert (
        round_termination_reason(session_cooldown, invoked=False, candidates=candidates)
        is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    )

    fresh_ledger = AgentFailureLedger(clock=clock)
    assert fresh_ledger.earliest_wait_ms(PHASE_RESOLUTION, candidates) == 0

    mixed_ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=5)
    mixed_ledger.record(
        AttemptFact(
            phase=PHASE_RESOLUTION,
            agent="primary",
            artifact_valid=False,
        )
    )
    assert mixed_ledger.earliest_wait_ms(PHASE_RESOLUTION, candidates) == 0


def test_an_agents_activity_event_cannot_kill_its_own_tool_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.agents.idle_watchdog.waiting_status_event import WaitingStatusEvent
    from ralph.agents.idle_watchdog.waiting_status_kind import WaitingStatusKind
    from ralph.pipeline.conflict_resolution.session import wrap_activity_listener

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock)
    session = ResolutionSession(ledger=ledger)
    listener = wrap_activity_listener(None, session)
    assert listener is not None
    listener(
        WaitingStatusEvent(
            kind=next(iter(WaitingStatusKind)),
            cumulative_seconds=1.0,
            current_run_seconds=1.0,
            idle_elapsed_seconds=1.0,
            ceiling_seconds=900.0,
            suspect_threshold_seconds=None,
            diagnostic={"last_activity_kind": "mcp_tool"},
            subagent_activity="reading tool_contract.py: the live Ralph-owned tool surface",
            stall_active=False,
        )
    )
    assert "claude" not in _cooling(session, ("claude",))
    assert session.terminal_reason is None
    assert ledger.is_available(PHASE_RESOLUTION, "claude") is True


def test_ralph_infrastructure_faults_are_not_recorded_as_a_chain_without_a_runnable_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline import auto_integrate_rebase_merge as rebase_merge

    captured: dict[str, object] = {}

    def _fake_resolve(root: Path, target: str, resolver: object, *, session: object) -> bool:
        captured["session"] = session
        session.halt_reason = "TOOL_SURFACE_DEAD: conflict markers survive in: a.py"
        session.charge_conflict_budget = False
        return False

    monkeypatch.setattr(rebase_merge, "resolve_rebase_in_progress", _fake_resolve)
    result = resolve_conflicted_rebase(tmp_path, "main", lambda *_a: False, None, None)
    assert result is not None
    short_circuit = result.short_circuit
    assert short_circuit is not None
    assert short_circuit.resolution_exhausted is False
    assert short_circuit.resolution_exhaustion_reason is None
    assert "TOOL_SURFACE_DEAD" not in (short_circuit.last_reason or ""), (
        "an infrastructure fault must not become exhaustion evidence"
    )


def test_a_real_no_runnable_resolver_stop_is_still_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline import auto_integrate_rebase_merge as rebase_merge

    def _fake_resolve(root: Path, target: str, resolver: object, *, session: object) -> bool:
        session.halt_reason = "ATTEMPT_FAILED: conflict markers survive in: a.py"
        return False

    monkeypatch.setattr(rebase_merge, "resolve_rebase_in_progress", _fake_resolve)
    result = resolve_conflicted_rebase(tmp_path, "main", lambda *_a: False, None, None)
    assert result is not None
    short_circuit = result.short_circuit
    assert short_circuit is not None
    assert short_circuit.last_reason == "ATTEMPT_FAILED: conflict markers survive in: a.py"
