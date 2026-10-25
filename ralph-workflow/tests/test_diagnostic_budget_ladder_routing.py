"""G6 invariants: the diagnostic-cap hand-off is gone; the ladder owns it.

Three properties are pinned here:

1. **Cap exhaustion reaches a ladder rung.** When the diagnostic-repair
   budget is spent the durable record carries the typed "diagnostic
   budget spent" outcome and the state escalates into the autonomous
   fallback ladder: a rung is recorded in the durable attempt ledger,
   the resolver-agent salvage attempt the last-resort precondition waits
   for is recorded, and the retired
   ``diagnostic_intervention_required`` hand-off flag is never set.
2. **Legacy flagged records load-and-clear into the ladder.** A record
   that still carries the retired flag on disk loads cleared (flag,
   held evidence and lease), so it re-enters the autonomous path
   instead of deadlocking on a person.
3. **No terminal ``INTERVENTION_REQUIRED`` display state is reachable
   from production inputs.** The display vocabulary keeps the retired
   values for parsing legacy payloads, but no production projection
   (record fact, recovery outcome, phase decision) renders the terminal
   intervention state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.display.auto_integrate_message import (
    OperationDisplayState,
    format_auto_integrate_message,
    format_integration_label,
)
from ralph.display.integration_display_label import IntegrationDisplayLabel
from ralph.pipeline._integration_diagnostic import (
    DIAGNOSTIC_BUDGET_SPENT_REASON,
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
)
from ralph.pipeline._integration_diagnostic_handoff import (
    budget_spent_record,
    escalate_diagnostic_budget_to_ladder,
    escalate_record_fact_outcome,
)
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    parse_record_payload,
    read_record,
    record_path,
    write_record,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from tests._integration_ladder_fakes import _FakePlumbing


def _record(**overrides: object) -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        **overrides,
    )


def test_cap_exhaustion_stamps_typed_outcome_and_reaches_a_ladder_rung(tmp_path: Path) -> None:
    """The spent budget escalates into the ladder; the hand-off flag stays False."""
    root = tmp_path / "repo"
    root.mkdir()
    ledger = AttemptLedger(root, ledger_path=root / ".agent" / "attempt-ledger.json")

    record = _record(merge_commit_repair_attempts=DIAGNOSTIC_REPAIR_ATTEMPT_CAP)
    stamped = budget_spent_record(record)
    assert stamped.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    assert stamped.diagnostic_intervention_required is False
    assert stamped.repair_cap_reason == DIAGNOSTIC_BUDGET_SPENT_REASON
    write_record(root, stamped)

    step = escalate_diagnostic_budget_to_ladder(
        root,
        stamped,
        ledger=ledger,
        git=_FakePlumbing(changed=()),
    )
    assert step.rung.value in {
        "normal_cleanup",
        "alternate_preservation",
        "quarantine",
        "fresh_worktree",
        "environment_degrade",
        "last_resort",
    }
    evidence = ledger.read().ladder_evidence
    assert any(key.startswith("rung:") for key in evidence)
    # The last-resort precondition (b) waits for the resolver-agent
    # salvage attempt; the diagnostic rotation IS that salvage.
    assert "salvage:resolver_agent" in evidence
    assert evidence["salvage:resolver_agent"].startswith("failed:")

    # The durable record still carries the typed outcome, never the flag.
    on_disk = read_record(root)
    assert on_disk is not None
    assert on_disk.diagnostic_intervention_required is False
    assert on_disk.repair_cap_reason == DIAGNOSTIC_BUDGET_SPENT_REASON


def test_stale_intent_outcome_routes_to_the_ladder(tmp_path: Path) -> None:
    """The typed stale-intent verdict routes into the ladder too."""
    root = tmp_path / "repo"
    root.mkdir()
    ledger = AttemptLedger(root, ledger_path=root / ".agent" / "attempt-ledger.json")

    step = escalate_record_fact_outcome(
        root,
        _record(),
        RecordFactOutcome.STALE_INTENT,
        reason="stale retained intent",
        ledger=ledger,
        git=_FakePlumbing(changed=()),
    )
    assert step.rung.value in {
        "normal_cleanup",
        "alternate_preservation",
        "quarantine",
        "fresh_worktree",
        "environment_degrade",
        "last_resort",
    }
    assert any(key.startswith("rung:") for key in ledger.read().ladder_evidence)


def test_legacy_flagged_record_loads_and_clears_into_the_ladder(tmp_path: Path) -> None:
    """A legacy flagged payload loads cleared and re-enters the ladder."""
    root = tmp_path / "repo"
    root.mkdir()
    payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": [["head", "0" * 40]],
        "diagnostic_intervention_required": True,
        "repair_cap_reason": "diagnostic repair exhausted; operator intervention required",
    }
    record_file = record_path(root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(json.dumps(payload), encoding="utf-8")

    parsed = parse_record_payload(payload)
    assert parsed is not None
    assert parsed.diagnostic_intervention_required is False
    assert parsed.diagnostic_evidence == ()
    assert parsed.diagnostic_ownership is None
    assert parsed.repair_cap_reason is None

    loaded = read_record(root)
    assert loaded is not None
    assert loaded.diagnostic_intervention_required is False

    # The cleared record routes into the ladder through the typed outcome.
    ledger = AttemptLedger(root, ledger_path=root / ".agent" / "attempt-ledger.json")
    escalate_diagnostic_budget_to_ladder(
        root,
        loaded,
        ledger=ledger,
        git=_FakePlumbing(changed=()),
    )
    assert any(key.startswith("rung:") for key in ledger.read().ladder_evidence)


def test_phase_decision_for_a_spent_budget_is_a_recovery_block(tmp_path: Path) -> None:
    """A spent budget blocks for recovery, never for a person."""
    from ralph.policy.loader import load_policy
    from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy

    policy = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete", on_loopback="development"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )
    del load_policy  # the policy above is the only input the decision needs

    record = _record(merge_commit_repair_attempts=DIAGNOSTIC_REPAIR_ATTEMPT_CAP)
    state = PipelineState(phase="development", rebase=RebaseState())
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=record,
        rebase_state=state.rebase,
    )
    assert decision is not PhaseDecision.REPORT_INTERVENTION_REQUIRED


def test_no_production_projection_renders_a_terminal_intervention_state() -> None:
    """No production projection renders the terminal intervention state.

    The display vocabulary keeps the retired values for parsing legacy
    payloads, but the live projections (the typed ladder escalation, the
    recovery outcome, the phase decision) never render them.
    """
    escalation = RebaseState(
        last_outcome=RebaseOutcomeKind.LADDER_ESCALATION,
        last_reason=(
            "diagnostic repair budget spent; escalating to fallback ladder rung "
            "alternate_preservation; preserved copy at refs/ralph-backup/abc123"
        ),
        recovery_record_retained=True,
    )
    label = format_integration_label(escalation)
    assert label != IntegrationDisplayLabel.INTERVENTION.value
    assert label == IntegrationDisplayLabel.EVIDENCE_DIAGNOSIS.value

    message = format_auto_integrate_message(rebase=escalation)
    assert "intervention required" not in message
    assert "ladder" in message
    # The message names the next autonomous action and the preserved copy.
    assert "alternate_preservation" in message
    assert "refs/ralph-backup/abc123" in message

    # The typed display state of the escalation is the diagnosis state,
    # never the terminal intervention state.
    assert (
        format_auto_integrate_message(
            operation_display_state=OperationDisplayState.EVIDENCE_DIAGNOSIS,
            reason="escalating to rung 2; preserved copy at refs/ralph-backup/abc123",
        )
        != f"{OperationDisplayState.INTERVENTION_REQUIRED}"
    )


@pytest.mark.parametrize(
    "outcome_value",
    ["ladder_escalation", "evidence_diagnosis"],
)
def test_escalation_outcomes_never_render_the_intervention_label(outcome_value: str) -> None:
    """The typed escalation labels never name the terminal intervention."""
    from ralph.display.integration_display_label import DISPLAY_LABEL_MAP

    assert DISPLAY_LABEL_MAP[outcome_value] != IntegrationDisplayLabel.INTERVENTION.value
