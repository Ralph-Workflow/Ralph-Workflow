from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_result import AttemptResult
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import ResolutionSession, invoke_resolution_agent
from ralph.pipeline.events import PipelineEvent
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.attempt_outcome import AttemptOutcome
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from tests._conflict_resolution_phase_parity_seams import _config, _policy_bundle

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _session(ledger: AgentFailureLedger, clock: FakeClock) -> ResolutionSession:
    return ResolutionSession(
        ledger=ledger,
        recovery_controller=RecoveryController(
            ledger=ledger, options=RecoveryControllerOptions(clock=clock)
        ),
    )


def _attempt_ended_by(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: UnavailabilityReason,
) -> ResolutionSession:
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock)
    session = _session(ledger, clock)

    def _runner(**_kwargs: object) -> AttemptResult:
        ledger.availability.mark_unavailable(
            PHASE_RESOLUTION, "minimax", reason, duration_ms=120_000
        )
        outcome = AttemptOutcome(AttemptOutcomeKind.UNKNOWN, None, None, str(reason))
        return AttemptResult(
            PipelineEvent.AGENT_FAILURE,
            AttemptFacts(artifact_valid=False),
            outcome,
            ((1, outcome),),
        )

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session.registered_runners",
        lambda: {AgentEntryPoint.CONFLICT_RESOLUTION: _runner},
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve\n", encoding="utf-8")
    invoke_resolution_agent(
        agent_name="minimax",
        prompt_path=prompt,
        config=_config(),
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        session=session,
    )
    return session


def test_a_provider_quota_refusal_is_not_recorded_as_an_agent_that_did_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = _attempt_ended_by(monkeypatch, tmp_path, UnavailabilityReason.OUT_OF_CREDITS)

    assert session.last_attempt_outcome is not ConflictAttemptOutcome.NO_ACTIVITY
    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE


def test_a_provider_quota_refusal_does_not_spend_the_conflict_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = _attempt_ended_by(monkeypatch, tmp_path, UnavailabilityReason.OUT_OF_CREDITS)

    assert session.last_failure_cause is ResolutionFailureCause.NONE
    assert session.charge_conflict_budget is False


def test_an_agent_that_exited_without_work_is_still_recorded_as_no_activity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = _attempt_ended_by(monkeypatch, tmp_path, UnavailabilityReason.NO_OUTPUT_AT_START)

    assert session.last_attempt_outcome is ConflictAttemptOutcome.NO_ACTIVITY
    assert session.last_failure_cause is ResolutionFailureCause.AGENT_FAILURE
