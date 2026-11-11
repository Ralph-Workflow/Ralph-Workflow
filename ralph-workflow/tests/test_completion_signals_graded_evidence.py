from __future__ import annotations

from ralph.agents.completion_signals import (
    CompletionSignals,
    completion_signals_terminal,
    graded_completion_signals,
    graded_verdict,
)
from ralph.pipeline.plumbing.smoke_evidence import Evidence, Provenance, absent
from ralph.pipeline.plumbing.smoke_provenance import Provenance as SmokeProvenance


def test_completion_signals_carries_evidence_typed_fields() -> None:
    artifact_evidence = Evidence(
        holds=True,
        provenance=Provenance.WIRE,
        detail="matched a tools/call ledger record",
    )
    sentinel_evidence = Evidence(
        holds=True,
        provenance=Provenance.WIRE,
        detail="declare_complete matched a tools/call ledger record",
    )
    signals = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=artifact_evidence,
        artifact_types=("plan",),
        completion_sentinel_present=sentinel_evidence,
        artifact_required=True,
        unsubmitted_draft_present=False,
    )
    assert signals.required_artifact_present is artifact_evidence
    assert signals.completion_sentinel_present is sentinel_evidence
    assert signals.required_artifact_evidence is artifact_evidence
    assert signals.completion_sentinel_evidence is sentinel_evidence


def test_completion_signals_terminal_continues_to_gate_on_holds() -> None:
    receipt_only = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched",
        ),
        artifact_types=("plan",),
        completion_sentinel_present=absent("no sentinel"),
        artifact_required=True,
        unsubmitted_draft_present=False,
    )
    assert completion_signals_terminal(receipt_only) is False

    completed = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched",
        ),
        artifact_types=("plan",),
        completion_sentinel_present=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="declare_complete matched",
        ),
        artifact_required=True,
        unsubmitted_draft_present=False,
    )
    assert completion_signals_terminal(completed) is True


def test_completion_signals_terminal_returns_false_when_sentinel_evidence_absent() -> None:
    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched",
        ),
        artifact_types=("plan",),
        completion_sentinel_present=absent("no sentinel on disk"),
        artifact_required=True,
        unsubmitted_draft_present=False,
    )
    assert completion_signals_terminal(signals) is False
    assert signals.completion_sentinel_present.holds is False
    assert signals.completion_sentinel_present.provenance is Provenance.ABSENT


def test_completion_signals_terminal_returns_false_when_artifact_evidence_absent() -> None:
    signals = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=absent("no artifact receipt"),
        artifact_types=(),
        completion_sentinel_present=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="declare_complete matched",
        ),
        artifact_required=True,
        unsubmitted_draft_present=False,
    )
    assert completion_signals_terminal(signals) is False


def test_completion_signals_terminal_returns_true_with_below_wire_evidence() -> None:
    signals = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=Evidence(
            holds=True,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail="promoted fallback receipt",
        ),
        artifact_types=("plan",),
        completion_sentinel_present=Evidence(
            holds=True,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail="sentinel receipt present",
        ),
        artifact_required=True,
        unsubmitted_draft_present=False,
    )
    assert completion_signals_terminal(signals) is True


def test_graded_verdict_returns_degraded_when_sentinel_missing() -> None:
    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched",
        ),
        artifact_types=("plan",),
        completion_sentinel_present=absent("completion sentinel was not observed"),
        artifact_required=True,
        unsubmitted_draft_present=False,
    )

    label, weakest = graded_verdict(signals)

    assert label == "DEGRADED"
    assert weakest == Provenance.ABSENT


def test_graded_verdict_returns_degraded_for_host_synthesized_sentinel() -> None:
    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched",
        ),
        artifact_types=("plan",),
        completion_sentinel_present=Evidence(
            holds=True,
            provenance=Provenance.HOST_SYNTHESIZED,
            detail="written by the harness",
        ),
        artifact_required=True,
        unsubmitted_draft_present=False,
    )

    label, weakest = graded_verdict(signals)

    assert label == "DEGRADED"
    assert weakest == Provenance.HOST_SYNTHESIZED


def test_graded_verdict_returns_pass_for_full_wire() -> None:
    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched",
        ),
        artifact_types=("plan",),
        completion_sentinel_present=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="declare_complete matched",
        ),
        artifact_required=True,
        unsubmitted_draft_present=False,
    )

    label, weakest = graded_verdict(signals)

    assert label == "PASS"
    assert weakest == Provenance.WIRE


def test_graded_completion_signals_returns_evidence_dict() -> None:
    artifact_evidence = Evidence(
        holds=True,
        provenance=Provenance.WIRE,
        detail="receipt matched",
    )
    sentinel_evidence = Evidence(
        holds=True,
        provenance=Provenance.WIRE,
        detail="declare_complete matched",
    )
    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=artifact_evidence,
        artifact_types=("plan",),
        completion_sentinel_present=sentinel_evidence,
        artifact_required=True,
        unsubmitted_draft_present=False,
    )

    evidence = graded_completion_signals(signals)

    assert evidence == {
        "required_artifact_present": artifact_evidence,
        "completion_sentinel_present": sentinel_evidence,
    }


def test_completion_signals_contract_fields_are_evidence_typed() -> None:
    artifact_evidence = Evidence(
        holds=True,
        provenance=Provenance.WIRE,
        detail="matched a tools/call ledger record",
    )
    sentinel_evidence = Evidence(
        holds=True,
        provenance=Provenance.WIRE,
        detail="declare_complete matched a tools/call ledger record",
    )
    signals = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=artifact_evidence,
        artifact_types=("plan",),
        completion_sentinel_present=sentinel_evidence,
        artifact_required=True,
        unsubmitted_draft_present=False,
    )
    assert isinstance(signals.required_artifact_present, Evidence)
    assert isinstance(signals.completion_sentinel_present, Evidence)
    assert signals.required_artifact_present.holds is True
    assert signals.completion_sentinel_present.holds is True


def test_smoke_provenance_matches_smoke_evidence_provenance() -> None:
    assert Provenance is SmokeProvenance
