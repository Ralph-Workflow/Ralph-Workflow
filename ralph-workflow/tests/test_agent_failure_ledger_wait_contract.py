from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.resolution_outcome import RetryLaterOutcome
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AttemptFact,
    Cooldown,
    Eligible,
)
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.recovery.wait_kind import WaitKind

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.driver import ResolutionInvoker
    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["a.py", "b.py"]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_earliest_wait_ms_partially_eligible_returns_zero": "operator rule: an agent is either on cooldown or not; the total unproductive limit now starts an expiring unproductive_limit cooldown instead of Spent, so agent-c is asserted to be cooling while the eligible agent keeps the wait at zero",
    "test_earliest_wait_ms_mixed_total_and_consecutive_cooldowns_returns_positive_int": "operator rule: an agent is either on cooldown or not; the total-limit agent is on an expiring cooldown instead of Spent, so the mixed chain wait is the earliest of two finite cooldowns",
    "test_earliest_wait_ms_single_total_limit_agent_returns_finite_wait": "operator rule: an agent is either on cooldown or not; the total unproductive limit now starts an expiring cooldown instead of a permanent Spent state, so earliest_wait_ms is a positive int that drops to zero after expiry instead of None",
    "test_earliest_wait_ms_all_total_limit_agents_return_finite_wait": "operator rule: an agent is either on cooldown or not; every agent past the total limit is on an expiring cooldown, so the chain wait is the earliest finite expiry instead of None",
    "test_controller_earliest_available_wait_ms_with_all_total_limit_ledger": "operator rule: an agent is either on cooldown or not; the total-limit agent is on an expiring cooldown in the shared tracker, so the controller reports its positive wait instead of zero",
    "test_controller_all_limit_agents_cooling_report_a_finite_wait": "operator rule: an agent is either on cooldown or not; all_exhausted no longer exists; agents past the total limit are cooling, so the chain reports a cooling-down wait and becomes available after expiry",
    "test_controller_handle_all_cooling_waits_for_the_earliest_expiry": "operator rule: an agent is either on cooldown or not; a chain whose agents are all cooling now yields the all-agents-unavailable wait state with the earliest expiry instead of entering the failed phase",
    "test_controller_earliest_eligible_wait_ms_reports_finite_wait_for_total_limit_chain": "operator rule: an agent is either on cooldown or not; the total-limit agent is on an expiring cooldown, so earliest_eligible_wait_ms reports its finite wait instead of None",
    "test_controller_earliest_eligible_wait_ms_separates_eligible_now_from_cooling": "operator rule: an agent is either on cooldown or not; note_retry_exhaustion is gone; exhausted retries start an expiring retry_limit cooldown, so every agent reports a finite wait and zero after expiry",
    "test_cooling_resolver_chain_is_not_tool_surface_dead": "operator rule: an agent is either on cooldown or not; the spent resolver and remember_dead_surface bar no longer exist; a chain of cooling resolvers reports CANDIDATE_UNAVAILABLE with a positive wait instead of a spent exhaustion",
    "test_cooled_resolvers_are_retried_after_their_cooldowns_expire": "operator rule: an agent is either on cooldown or not; a cooled resolver is no longer barred for the run, so after the cooldowns expire the resolvers are invoked again instead of the chain being reported exhausted",
}
_CONFIG = UnifiedConfig.model_validate({"general": {}})


def _minimal_policy_bundle() -> PolicyBundle:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _install_conflict_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.git.conflict_stage_entries import ConflictStageEntries
    from ralph.pipeline.conflict_resolution import sight as sight_module

    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: list(_CONFLICTED))
    monkeypatch.setattr(
        driver_module,
        "paths_with_conflict_markers",
        lambda _root, _paths: list(_CONFLICTED),
    )
    monkeypatch.setattr(
        driver_module,
        "resolution_chain_agents",
        lambda _bundle: ("primary", "fallback"),
    )
    monkeypatch.setattr(
        driver_module,
        "render_conflict_prompt",
        lambda *, root, **_kwargs: root / "resolution-prompt.md",
    )
    monkeypatch.setattr(
        sight_module,
        "conflict_stage_entries",
        lambda _root, _paths: ConflictStageEntries({}),
    )


class _NoSleepClock:
    def monotonic(self) -> float:
        return 0.0

    def sleep(self, seconds: float) -> None:
        del seconds


def _run_cooling_resolver_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cooldown_elapsed: bool,
    invoke: ResolutionInvoker | None = None,
) -> tuple[RecoveryController, ResolutionSession]:
    _install_conflict_seams(monkeypatch)
    clock = FakeClock(start=0.0)
    policy_bundle = _minimal_policy_bundle()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=policy_bundle,
            event_bus=FailureEventBus(),
        )
    )
    for _ in range(controller.ledger.consecutive_limit):
        controller.record_attempt_fact(
            PHASE_RESOLUTION, "primary", artifact_valid=False, workspace_changed=False
        )
    controller.unavailability_store.mark_unavailable(
        PHASE_RESOLUTION, "fallback", UnavailabilityReason.NO_OUTPUT_AT_START
    )
    if cooldown_elapsed:
        clock.advance(60.0)

    session = ResolutionSession(recovery_controller=controller)
    session.rounds_spent = 1

    token = install_executors(clock=_NoSleepClock())
    try:
        success = run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_CONFIG,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=policy_bundle,
            display=None,
            display_context=None,
            invoke=invoke,
            session=session,
            strategy_history=("previous-strategy",),
        )
    finally:
        reset_executors(token)

    assert success is False
    return controller, session


def test_earliest_wait_ms_empty_agents_returns_zero() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock)

    assert ledger.earliest_wait_ms("development", []) == 0
    assert ledger.earliest_wait_ms("development", ()) == 0


def test_earliest_wait_ms_eligible_agents_returns_zero() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock)

    assert ledger.earliest_wait_ms("development", ["agent-a"]) == 0
    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b"]) == 0


def test_earliest_wait_ms_partially_eligible_returns_zero() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2, total_limit=4)

    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    outcome_a = ledger.record(
        AttemptFact(phase="development", agent="agent-a", artifact_valid=False)
    )
    assert isinstance(outcome_a.status, Cooldown)

    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b"]) == 0

    for _ in range(4):
        ledger.record(
            AttemptFact(
                phase="development",
                agent="agent-c",
                artifact_valid=False,
                workspace_changed=True,
            )
        )
    status_c = ledger.status("development", "agent-c")
    assert isinstance(status_c, Cooldown)
    assert status_c.reason == str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)

    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b", "agent-c"]) == 0


def test_earliest_wait_ms_cooldown_returns_positive_int() -> None:
    clock = FakeClock(start=10.0)
    ledger = AgentFailureLedger(
        clock=clock,
        consecutive_limit=2,
        base_cooldown_seconds=30.0,
    )

    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))

    wait_a = ledger.earliest_wait_ms("development", ["agent-a"])
    assert wait_a == 30_000
    assert isinstance(wait_a, int)
    assert wait_a > 0

    clock.advance(5.0)
    ledger.record(AttemptFact(phase="development", agent="agent-b", artifact_valid=False))
    ledger.record(AttemptFact(phase="development", agent="agent-b", artifact_valid=False))

    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b"]) == 25_000


def test_earliest_wait_ms_mixed_total_and_consecutive_cooldowns_returns_positive_int() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2, total_limit=3)

    for _ in range(3):
        ledger.record(
            AttemptFact(
                phase="development",
                agent="agent-a",
                artifact_valid=False,
                workspace_changed=True,
            )
        )
    assert isinstance(ledger.status("development", "agent-a"), Cooldown)

    clock.advance(10.0)
    ledger.record(AttemptFact(phase="development", agent="agent-b", artifact_valid=False))
    ledger.record(AttemptFact(phase="development", agent="agent-b", artifact_valid=False))
    assert isinstance(ledger.status("development", "agent-b"), Cooldown)

    wait = ledger.earliest_wait_ms("development", ["agent-a", "agent-b"])
    assert wait == 50_000
    assert isinstance(wait, int)
    assert wait > 0


def test_earliest_wait_ms_expired_cooldown_returns_zero() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2, base_cooldown_seconds=10.0)

    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    assert isinstance(ledger.status("development", "agent-a"), Cooldown)

    clock.advance(10.0)
    assert ledger.earliest_wait_ms("development", ["agent-a"]) == 0
    assert isinstance(ledger.status("development", "agent-a"), Eligible)


def test_earliest_wait_ms_single_total_limit_agent_returns_finite_wait() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=2)

    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    assert isinstance(ledger.status("development", "agent-a"), Cooldown)
    wait = ledger.earliest_wait_ms("development", ["agent-a"])
    assert wait == 60_000
    clock.advance(60.0)
    assert ledger.earliest_wait_ms("development", ["agent-a"]) == 0


def test_earliest_wait_ms_all_total_limit_agents_return_finite_wait() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=2)

    for agent in ("agent-a", "agent-b", "agent-c"):
        clock.advance(1.0)
        for _ in range(2):
            ledger.record(
                AttemptFact(
                    phase="development",
                    agent=agent,
                    artifact_valid=False,
                    workspace_changed=True,
                )
            )
        assert isinstance(ledger.status("development", agent), Cooldown)

    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b", "agent-c"]) == 58_000
    assert ledger.earliest_wait_ms("development", ("agent-a", "agent-b", "agent-c")) == 58_000


def test_controller_earliest_available_wait_ms_with_all_total_limit_ledger() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    assert ledger.earliest_wait_ms("development", ["agent-a"]) == 60_000

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )

    wait_ms = controller.earliest_available_wait_ms("development", ["agent-a"])
    assert wait_ms == 60_000
    assert isinstance(wait_ms, int)


def test_controller_earliest_available_wait_ms_with_cooldown_ledger() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, base_cooldown_seconds=30.0)
    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    assert ledger.earliest_wait_ms("development", ["agent-a"]) == 30_000

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )

    wait_ms = controller.earliest_available_wait_ms("development", ["agent-a"])
    assert wait_ms == 30_000


def test_controller_earliest_available_wait_ms_takes_min_of_tracker_and_ledger() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, base_cooldown_seconds=45.0)
    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    clock.advance(15.0)
    ledger.record(AttemptFact(phase="development", agent="agent-b", artifact_valid=False))
    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b"]) == 30_000

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            unavailable_timeouts={"agent-a": 50_000, "agent-b": 35_000},
        ),
    )

    assert controller.earliest_available_wait_ms("development", ["agent-a", "agent-b"]) == 20_000


def test_controller_all_limit_agents_cooling_report_a_finite_wait() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-b",
            artifact_valid=False,
            workspace_changed=True,
        )
    )

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )

    wait = controller.eligibility_wait("development", ["agent-a", "agent-b"])
    assert wait.kind is WaitKind.COOLING_DOWN
    assert wait.wait_ms == 60_000
    clock.advance(60.0)
    assert controller.agents_now_available("development", ["agent-a", "agent-b"]) == [
        "agent-a",
        "agent-b",
    ]


def test_controller_handle_all_cooling_waits_for_the_earliest_expiry() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1)

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )

    chain_state = AgentChainState(
        agents=["agent-a", "agent-b"],
        current_index=1,
        retries=0,
    )
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    agent_a = ledger.status("development", "agent-a")
    assert isinstance(agent_a, Cooldown)

    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")

    context = FailureContext(phase="development", agent="agent-b")
    new_state, effects, _evt = controller.handle(
        state,
        raw_failure="agent-b failed",
        context=context,
        workspace_changed=True,
        artifact_valid=False,
    )

    assert isinstance(ledger.status("development", "agent-b"), Cooldown)
    assert new_state.is_waiting_state
    assert new_state.last_retry_delay_ms == agent_a.until_ms
    assert new_state.phase == "development"
    assert effects == []


def test_controller_earliest_eligible_wait_ms_reports_finite_wait_for_total_limit_chain() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )

    assert controller.earliest_eligible_wait_ms("development", ["agent-a"]) == 60_000
    assert controller.earliest_available_wait_ms("development", ["agent-a"]) == 60_000


def test_controller_earliest_eligible_wait_ms_separates_eligible_now_from_cooling() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )
    for _ in range(controller.ledger.consecutive_limit):
        controller.record_attempt("development", "retried-agent")
    controller.unavailability_store.mark_unavailable(
        "development", "cooled-agent", UnavailabilityReason.NO_OUTPUT_AT_START
    )

    retried = controller.agent_eligibility("development", "retried-agent")
    assert isinstance(retried, Cooldown)
    assert retried.reason == str(UnavailabilityReason.RETRY_LIMIT)
    assert controller.earliest_eligible_wait_ms("development", ["retried-agent"]) == 60_000
    assert controller.earliest_eligible_wait_ms("development", ["cooled-agent"]) == 5000
    assert (
        controller.earliest_eligible_wait_ms("development", ["retried-agent", "cooled-agent"])
        == 5000
    )
    assert controller.earliest_eligible_wait_ms("development", []) is None

    clock.advance(60.0)

    assert (
        controller.earliest_eligible_wait_ms("development", ["retried-agent", "cooled-agent"]) == 0
    )


@pytest.mark.timeout_seconds(10)
def test_cooling_resolver_chain_is_not_tool_surface_dead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    controller, session = _run_cooling_resolver_chain(
        monkeypatch,
        tmp_path,
        cooldown_elapsed=False,
    )

    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["primary", "fallback"]) > 0
    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    assert session.terminal_reason is not ResolutionTerminationReason.TOOL_SURFACE_DEAD


@pytest.mark.timeout_seconds(10)
def test_cooled_resolvers_are_retried_after_their_cooldowns_expire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invoked: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> RetryLaterOutcome:
        del prompt_path, round_index
        invoked.append(agent_name)
        return RetryLaterOutcome(on_change="resolver produced no resolution")

    controller, _session = _run_cooling_resolver_chain(
        monkeypatch,
        tmp_path,
        cooldown_elapsed=True,
        invoke=_invoke,
    )

    assert invoked
    assert invoked[0] == "primary"
    assert "fallback" in invoked
    assert controller.earliest_eligible_wait_ms(PHASE_RESOLUTION, []) is None
