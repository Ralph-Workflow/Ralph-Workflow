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
def test_failure_kinds_use_shared_finalization(
    issuer: TerminationIssuer, status: str
) -> None:
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
