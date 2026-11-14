from __future__ import annotations

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.effect_executor import demotion_after_finalization
from ralph.policy.validation.artifact_contract import ArtifactContractMismatchError
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

PHASE = "development"
AGENT = "worker"
AGENT_FAILURE_TEXTS = (
    "agent produced nothing while checking config",
    "agent produced nothing after checkpoint instructions",
    "agent produced nothing while reading policy",
)


def _counts(ledger: AgentFailureLedger) -> tuple[int, int]:
    entries = ledger.snapshot()["entries"]
    assert isinstance(entries, dict)
    entry = entries.get(f"{PHASE}:{AGENT}")
    if not isinstance(entry, dict):
        return (0, 0)
    return (
        int(entry["consecutive_unproductive"]),
        int(entry["total_unproductive"]),
    )


def _configuration_failure() -> ArtifactContractMismatchError:
    return ArtifactContractMismatchError(
        phase=PHASE,
        prompted_type=None,
        graded_type="development_result",
    )


def test_controller_regression_engine_origin_does_not_charge_ledger() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(clock=FakeClock(start=0.0)))

    controller.finalize_attempt(
        PHASE,
        AGENT,
        facts=AttemptFacts(artifact_valid=False, workspace_changed=False),
        attempt_failed=True,
        exc=_configuration_failure(),
        retryable_reason="configuration fault",
    )

    assert _counts(controller.ledger) == (0, 0)


@pytest.mark.parametrize("failure_text", AGENT_FAILURE_TEXTS)
def test_controller_regression_agent_text_does_charge_ledger(failure_text: str) -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(clock=FakeClock(start=0.0)))

    controller.finalize_attempt(
        PHASE,
        AGENT,
        facts=AttemptFacts(artifact_valid=False, workspace_changed=False),
        attempt_failed=True,
        exc=RuntimeError(failure_text),
        retryable_reason=failure_text,
    )

    assert _counts(controller.ledger) == (1, 1)


def test_ledger_fallback_regression_engine_origin_does_not_charge_ledger() -> None:
    ledger = AgentFailureLedger(clock=FakeClock(start=0.0))

    demotion_after_finalization(
        None,
        ledger,
        phase=PHASE,
        agent=AGENT,
        workspace_changed=False,
        exc=_configuration_failure(),
    )

    assert _counts(ledger) == (0, 0)


@pytest.mark.parametrize("failure_text", AGENT_FAILURE_TEXTS)
def test_ledger_fallback_regression_agent_text_does_charge_ledger(failure_text: str) -> None:
    ledger = AgentFailureLedger(clock=FakeClock(start=0.0))

    demotion_after_finalization(
        None,
        ledger,
        phase=PHASE,
        agent=AGENT,
        workspace_changed=False,
        exc=RuntimeError(failure_text),
    )

    assert _counts(ledger) == (1, 1)
