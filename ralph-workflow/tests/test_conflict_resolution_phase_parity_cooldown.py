from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    SupervisionInfrastructureError,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import run_loop
from ralph.pipeline.auto_integrate import _configured_resolver_allowed
from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
)
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.run_loop import cold_capped_escalation_due
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from tests._conflict_resolution_phase_parity_seams import (
    _config,
    _install_seams,
    _policy_bundle,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_shared_eligibility_governs_run_loop_escalation_and_cooldown": (
        "launch permission and cold-capped escalation follow shared any_eligible / "
        "all_exhausted; durable resolution_exhausted is not a launch veto"
    ),
    "test_conflict_eligibility_wait_uses_shared_handle_cooldown_delay": (
        "conflict-driver eligibility waits must sleep through ordinary "
        "handle_cooldown_delay rather than an integration-only advance/_sleep fork"
    ),
    "test_revive_cooling_candidates_uses_shared_handle_cooldown_delay": (
        "in-round revive waits for cooling candidates must sleep through ordinary "
        "handle_cooldown_delay rather than a hasattr(advance)/_sleep fork"
    ),
}


class _CooldownClock(FakeClock):
    def now(self) -> float:
        return self.monotonic()


def test_conflict_waits_for_earliest_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = _CooldownClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            unavailability_entries={
                name: UnavailabilityEntry(
                    unavailable_until_ms=delay,
                    reason=UnavailabilityReason.NO_OUTPUT_AT_START,
                    attempt=0,
                    base_backoff_ms=5000,
                    max_backoff_ms=18000000,
                )
                for name, delay in (("one", 9000), ("two", 5000))
            },
        )
    )
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    called: list[str] = []

    def invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        return True

    token = install_executors(clock=clock)
    try:
        _install_seams(monkeypatch, surviving_per_round=[[]])
        assert run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=invoke,
            session=ResolutionSession(recovery_controller=controller),
        )
    finally:
        reset_executors(token)
    assert called == ["two"]
    assert clock.monotonic() == 5.0
    assert (
        controller.preferred_agent_index("rebase_conflict_resolution", ["one", "two"]).agent
        == "two"
    )


def test_conflict_eligibility_wait_uses_shared_handle_cooldown_delay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = _CooldownClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            unavailability_entries={
                "one": UnavailabilityEntry(
                    unavailable_until_ms=7000,
                    reason=UnavailabilityReason.NO_OUTPUT_AT_START,
                    attempt=1,
                    base_backoff_ms=5000,
                    max_backoff_ms=18000000,
                )
            },
        )
    )
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    delay_ms_seen: list[int] = []
    real_handle = run_loop.handle_cooldown_delay

    def _spy_handle(state: object, ctx: object) -> object:
        delay = getattr(state, "last_retry_delay_ms", 0)
        if isinstance(delay, int) and delay > 0:
            delay_ms_seen.append(delay)
        return real_handle(state, ctx)  # type: ignore[arg-type]

    monkeypatch.setattr(run_loop, "handle_cooldown_delay", _spy_handle)
    if hasattr(driver_module, "handle_cooldown_delay"):
        monkeypatch.setattr(driver_module, "handle_cooldown_delay", _spy_handle)

    called_at: list[float] = []

    def invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called_at.append(clock.monotonic())
        return True

    token = install_executors(clock=clock)
    try:
        _install_seams(monkeypatch, surviving_per_round=[[]])
        assert run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=invoke,
            session=ResolutionSession(recovery_controller=controller),
        )
    finally:
        reset_executors(token)

    assert delay_ms_seen == [7000]
    assert called_at == [7.0]
    assert clock.monotonic() == 7.0
    assert controller.agent_eligibility("rebase_conflict_resolution", "one").is_eligible
    assert not hasattr(driver_module, "_wait_for_candidates_if_cooling_down")


def test_revive_cooling_candidates_uses_shared_handle_cooldown_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph.pipeline.conflict_resolution import _driver_rounds as rounds_module

    clock = _CooldownClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            unavailability_entries={
                "one": UnavailabilityEntry(
                    unavailable_until_ms=5000,
                    reason=UnavailabilityReason.NO_OUTPUT_AT_START,
                    attempt=1,
                    base_backoff_ms=5000,
                    max_backoff_ms=18000000,
                )
            },
        )
    )
    session = ResolutionSession(recovery_controller=controller)
    delay_ms_seen: list[int] = []
    real_handle = run_loop.handle_cooldown_delay

    def _spy_handle(state: object, ctx: object) -> object:
        delay = getattr(state, "last_retry_delay_ms", 0)
        if isinstance(delay, int) and delay > 0:
            delay_ms_seen.append(delay)
        return real_handle(state, ctx)  # type: ignore[arg-type]

    monkeypatch.setattr(run_loop, "handle_cooldown_delay", _spy_handle)
    if hasattr(rounds_module, "handle_cooldown_delay"):
        monkeypatch.setattr(rounds_module, "handle_cooldown_delay", _spy_handle)

    revived = rounds_module._revive_cooling_candidates(
        session,
        ("one",),
        set(),
        frozenset(),
        cohort_wait_ms=5000,
    )

    assert revived is True
    assert delay_ms_seen == [5000]
    assert clock.monotonic() == 5.0
    assert controller.agent_eligibility("rebase_conflict_resolution", "one").is_eligible
    import inspect

    source = inspect.getsource(rounds_module._revive_cooling_candidates)
    assert "handle_cooldown_delay" in source
    assert 'hasattr(controller._clock, "advance")' not in source


@pytest.mark.parametrize(
    ("label", "exc"),
    [
        ("nonzero_exit", AgentInvocationError("agent", 1, "exit 1")),
        ("timeout", AgentInactivityTimeoutError("agent", 30.0)),
        ("invalid_result", None),
        ("provider_failure", SupervisionInfrastructureError("agent", "connection refused")),
    ],
)
def test_fault_injection_phase_classification_and_cooldown_parity(
    label: str,
    exc: BaseException | None,
) -> None:
    del label
    clock_dev = FakeClock(start=0.0)
    clock_res = FakeClock(start=0.0)
    ctrl_dev = RecoveryController(options=RecoveryControllerOptions(clock=clock_dev))
    ctrl_res = RecoveryController(options=RecoveryControllerOptions(clock=clock_res))
    facts = AttemptFacts(artifact_valid=False, workspace_changed=False)

    outcome_dev = ctrl_dev.finalize_attempt(
        "development",
        "agent",
        facts=facts,
        attempt_failed=True,
        exc=exc,
    )
    outcome_res = ctrl_res.finalize_attempt(
        "rebase_conflict_resolution",
        "agent",
        facts=facts,
        attempt_failed=True,
        exc=exc,
    )

    assert outcome_dev.kind == outcome_res.kind
    assert outcome_dev.reason == outcome_res.reason

    status_dev = ctrl_dev.ledger.status("development", "agent")
    status_res = ctrl_res.ledger.status("rebase_conflict_resolution", "agent")
    assert type(status_dev) is type(status_res)
    assert status_dev.is_eligible == status_res.is_eligible
    assert status_dev.is_cooldown == status_res.is_cooldown
    assert status_dev.is_spent == status_res.is_spent


@pytest.mark.parametrize(
    ("label", "reason"),
    [
        ("nonzero_exit", UnavailabilityReason.PROCESS_EXITED),
        ("timeout", UnavailabilityReason.SUSPICIOUS_TIMEOUT_NO_OUTPUT),
        ("invalid_result", UnavailabilityReason.NO_OUTPUT_AT_START),
        ("provider_failure", UnavailabilityReason.PROVIDER_UNAVAILABLE),
    ],
)
def test_fault_cooldown_prevents_relaunch_until_fake_clock_eligible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    label: str,
    reason: UnavailabilityReason,
) -> None:
    del label
    clock = _CooldownClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            unavailability_entries={
                "one": UnavailabilityEntry(
                    unavailable_until_ms=7000,
                    reason=reason,
                    attempt=1,
                    base_backoff_ms=5000,
                    max_backoff_ms=18000000,
                )
            },
        )
    )
    assert not controller.agent_eligibility("rebase_conflict_resolution", "one").is_eligible
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    called_at: list[float] = []

    def invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called_at.append(clock.monotonic())
        return True

    token = install_executors(clock=clock)
    try:
        _install_seams(monkeypatch, surviving_per_round=[[]])
        assert run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=invoke,
            session=ResolutionSession(recovery_controller=controller),
        )
    finally:
        reset_executors(token)

    assert called_at == [7.0]
    assert clock.monotonic() == 7.0
    assert controller.agent_eligibility("rebase_conflict_resolution", "one").is_eligible


def test_shared_eligibility_governs_run_loop_escalation_and_cooldown() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, base_cooldown_seconds=10.0)
    candidates = ("res-1",)

    assert ledger.any_eligible("rebase_conflict_resolution", candidates)
    state = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=1,
        resolution_exhausted=True,
    )
    ident = ConflictIdentity(feature_sha="f", target_sha="t")

    class _Ctx:
        cold_capped_escalation_attempted: bool = False

    ctx = _Ctx()

    assert not cold_capped_escalation_due(
        ctx,
        state,
        "main",
        ident,
        ledger=ledger,
        candidates=candidates,
    )

    ledger.record(
        AttemptFact(phase="rebase_conflict_resolution", agent="res-1", artifact_valid=False)
    )
    assert not ledger.any_eligible("rebase_conflict_resolution", candidates)
    assert not ledger.all_exhausted("rebase_conflict_resolution", candidates)
    assert not _configured_resolver_allowed(
        state, "main", ident, ledger=ledger, candidates=candidates
    )

    assert not cold_capped_escalation_due(
        ctx,
        state,
        "main",
        ident,
        ledger=ledger,
        candidates=candidates,
    )

    clock.advance(10.0)
    assert ledger.any_eligible("rebase_conflict_resolution", candidates)
    assert not cold_capped_escalation_due(
        ctx,
        state,
        "main",
        ident,
        ledger=ledger,
        candidates=candidates,
    )

    spent_ledger = AgentFailureLedger(
        clock=clock,
        consecutive_limit=1,
        total_limit=1,
        base_cooldown_seconds=10.0,
    )
    spent_ledger.record(
        AttemptFact(phase="rebase_conflict_resolution", agent="res-1", artifact_valid=False)
    )
    assert spent_ledger.all_exhausted("rebase_conflict_resolution", candidates)
    assert not _configured_resolver_allowed(
        state, "main", ident, ledger=spent_ledger, candidates=candidates
    )
    assert cold_capped_escalation_due(
        ctx,
        state,
        "main",
        ident,
        ledger=spent_ledger,
        candidates=candidates,
    )
