from __future__ import annotations

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.conflict_resolution_config import ConflictResolutionConfig
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, Cooldown
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions


@pytest.mark.parametrize(
    ("issuer", "status"),
    (
        (TerminationIssuer.AGENT_EXIT, "exit:1"),
        (TerminationIssuer.AGENT_EXIT, "signal:15"),
        (TerminationIssuer.SUPERVISOR_IDLE, "watchdog_kill"),
        (TerminationIssuer.RECOVERY_TIMEOUT, "timeout"),
        (TerminationIssuer.UNKNOWN, "provider_failure"),
        (TerminationIssuer.UNKNOWN, "invalid_result"),
    ),
)
def test_failure_kinds_use_shared_finalization(issuer: TerminationIssuer, status: str) -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(clock=FakeClock()))
    outcome = controller.finalize_attempt(
        PHASE_RESOLUTION,
        "resolver",
        facts=AttemptFacts(
            exit_status=status,
            termination=TerminationRecord.create(issuer),
        ),
        attempt_failed=True,
    )
    assert controller.last_attempt_outcome is outcome


@pytest.mark.parametrize("boundary", ("restated_prompt", "rung", "strategy", "restart"))
def test_cooldown_survives_non_identity_boundaries(boundary: str) -> None:
    del boundary
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock, ledger=ledger))
    controller.finalize_attempt(
        PHASE_RESOLUTION,
        "first",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )
    restored = AgentFailureLedger.from_snapshot(ledger.snapshot(), clock=clock)
    assert isinstance(restored.agent_eligibility(PHASE_RESOLUTION, "first"), Cooldown)


def test_fall_over_then_wait_with_two_agents() -> None:
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock, ledger=ledger))
    for agent in ("first", "second"):
        controller.finalize_attempt(
            PHASE_RESOLUTION,
            agent,
            facts=AttemptFacts(artifact_valid=False),
            attempt_failed=True,
        )
    assert controller.preferred_agent_index(PHASE_RESOLUTION, ("first", "second")).agent is None
    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ("first", "second")) > 0


def test_resolver_config_has_no_active_resolution_cap() -> None:
    assert "total_resolution_cap_seconds" not in ConflictResolutionConfig.model_fields


@pytest.mark.parametrize(
    ("issuer", "status"),
    (
        (TerminationIssuer.AGENT_EXIT, "exit:1"),
        (TerminationIssuer.AGENT_EXIT, "signal:15"),
        (TerminationIssuer.SUPERVISOR_IDLE, "watchdog_kill"),
        (TerminationIssuer.RECOVERY_TIMEOUT, "timeout"),
        (TerminationIssuer.UNKNOWN, "provider_failure"),
        (TerminationIssuer.UNKNOWN, "invalid_result"),
    ),
)
def test_resolver_and_ordinary_phase_produce_identical_classification_and_lifecycle(
    issuer: TerminationIssuer, status: str
) -> None:
    clock_res = FakeClock(start=100.0)
    ledger_res = AgentFailureLedger(clock=clock_res, consecutive_limit=1)
    controller_res = RecoveryController(
        options=RecoveryControllerOptions(clock=clock_res, ledger=ledger_res)
    )

    clock_dev = FakeClock(start=100.0)
    ledger_dev = AgentFailureLedger(clock=clock_dev, consecutive_limit=1)
    controller_dev = RecoveryController(
        options=RecoveryControllerOptions(clock=clock_dev, ledger=ledger_dev)
    )

    facts = AttemptFacts(
        exit_status=status,
        termination=TerminationRecord.create(issuer),
    )

    outcome_res = controller_res.finalize_attempt(
        PHASE_RESOLUTION,
        "agent-1",
        facts=facts,
        attempt_failed=True,
    )
    outcome_dev = controller_dev.finalize_attempt(
        "development",
        "agent-1",
        facts=facts,
        attempt_failed=True,
    )

    assert outcome_res.kind == outcome_dev.kind
    assert outcome_res.reason == outcome_dev.reason

    eligibility_res = controller_res.agent_eligibility(PHASE_RESOLUTION, "agent-1")
    eligibility_dev = controller_dev.agent_eligibility("development", "agent-1")
    assert eligibility_res == eligibility_dev

    wait_res = controller_res.earliest_available_wait_ms(PHASE_RESOLUTION, ("agent-1",))
    wait_dev = controller_dev.earliest_available_wait_ms("development", ("agent-1",))
    assert wait_res == wait_dev
    assert wait_res > 0

    sel_res = controller_res.preferred_agent_index(PHASE_RESOLUTION, ("agent-1",))
    sel_dev = controller_dev.preferred_agent_index("development", ("agent-1",))
    assert sel_res.agent is None
    assert sel_dev.agent is None
    assert sel_res.index == sel_dev.index

    clock_res.advance(wait_res / 1000.0 + 1.0)
    clock_dev.advance(wait_dev / 1000.0 + 1.0)

    sel_after_res = controller_res.preferred_agent_index(PHASE_RESOLUTION, ("agent-1",))
    sel_after_dev = controller_dev.preferred_agent_index("development", ("agent-1",))
    assert sel_after_res.agent == "agent-1"
    assert sel_after_dev.agent == "agent-1"


def test_resolver_and_ordinary_phase_multi_candidate_selection_parity() -> None:
    clock_res = FakeClock(start=50.0)
    ledger_res = AgentFailureLedger(clock=clock_res, consecutive_limit=1)
    controller_res = RecoveryController(
        options=RecoveryControllerOptions(clock=clock_res, ledger=ledger_res)
    )

    clock_dev = FakeClock(start=50.0)
    ledger_dev = AgentFailureLedger(clock=clock_dev, consecutive_limit=1)
    controller_dev = RecoveryController(
        options=RecoveryControllerOptions(clock=clock_dev, ledger=ledger_dev)
    )

    candidates = ("agent-1", "agent-2", "agent-3")

    sel_res_init = controller_res.preferred_agent_index(PHASE_RESOLUTION, candidates)
    sel_dev_init = controller_dev.preferred_agent_index("development", candidates)
    assert sel_res_init.agent == "agent-1"
    assert sel_dev_init.agent == "agent-1"

    controller_res.finalize_attempt(
        PHASE_RESOLUTION,
        "agent-1",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )
    controller_dev.finalize_attempt(
        "development",
        "agent-1",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )

    sel_res_after_1 = controller_res.preferred_agent_index(PHASE_RESOLUTION, candidates)
    sel_dev_after_1 = controller_dev.preferred_agent_index("development", candidates)
    assert sel_res_after_1.agent == "agent-2"
    assert sel_dev_after_1.agent == "agent-2"

    controller_res.finalize_attempt(
        PHASE_RESOLUTION,
        "agent-2",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )
    controller_dev.finalize_attempt(
        "development",
        "agent-2",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )

    sel_res_after_2 = controller_res.preferred_agent_index(PHASE_RESOLUTION, candidates)
    sel_dev_after_2 = controller_dev.preferred_agent_index("development", candidates)
    assert sel_res_after_2.agent == "agent-3"
    assert sel_dev_after_2.agent == "agent-3"


def test_controller_has_no_secondary_conflict_classifier() -> None:
    assert not hasattr(RecoveryController, "classify_conflict_attempt")
    assert not hasattr(RecoveryController, "_typed_conflict_attempt_reason")
