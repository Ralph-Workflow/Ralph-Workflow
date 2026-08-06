"""S-4: ``CompletionSignals`` carries graded ``Evidence`` for the general pipeline.

Brief ``.agent/PRODUCT_CRITERIA.md`` F6 / DoD 12, 14, 16. The
Provenance/Evidence lattice that F1 shipped for the smoke gate must
bind the *general* completion contract, not just the smoke harness,
so that:

- Every phase that produces an artifact reports ``required_artifact_evidence``
  and ``completion_sentinel_evidence`` as ``Evidence`` values (typed,
  provenance-bearing) -- never as bare ``bool``.
- ``graded_verdict()`` derives the operator-facing verdict from the
  weakest provenance backing any required fact, so a phase whose
  completion sentinel is missing or whose artifact receipt is below
  ``WIRE`` reports the honestly-graded verdict, not ``"success"``.
- ``completion_signals_terminal`` continues to gate on ``.holds`` so
  existing callers keep working unchanged.

The tests below pin those invariants so a future contributor cannot
silently strip the grading back to bare booleans.
"""

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
    """``CompletionSignals`` exposes graded ``Evidence``-typed fields.

    Post-S-4, ``required_artifact_evidence`` and
    ``completion_sentinel_evidence`` are required ``Evidence`` values
    on the dataclass (typed at construction; the type system forbids
    re-introducing a bare ``bool``).
    """
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
        required_artifact_present=False,
        artifact_types=("plan",),
        completion_sentinel_present=False,
        artifact_required=True,
        unsubmitted_draft_present=False,
        required_artifact_evidence=artifact_evidence,
        completion_sentinel_evidence=sentinel_evidence,
    )
    assert signals.required_artifact_evidence is artifact_evidence
    assert signals.completion_sentinel_evidence is sentinel_evidence


def test_completion_signals_terminal_continues_to_gate_on_holds() -> None:
    """Existing ``completion_signals_terminal`` semantics are preserved.

    The bool fields (``required_artifact_present``,
    ``completion_sentinel_present``) keep their pre-S-4 meaning so the
    existing callers (``_pty_line_reader.py``,
    ``execution_state/_helpers.py``, ``_completion_mixin.py``) and the
    50+ test call sites continue to work unchanged.
    """
    receipt_only = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=True,
        artifact_types=("plan",),
        completion_sentinel_present=False,
        artifact_required=True,
        unsubmitted_draft_present=False,
        required_artifact_evidence=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched",
        ),
        completion_sentinel_evidence=absent("no sentinel"),
    )
    assert completion_signals_terminal(receipt_only) is False

    completed = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=True,
        artifact_types=("plan",),
        completion_sentinel_present=True,
        artifact_required=True,
        unsubmitted_draft_present=False,
        required_artifact_evidence=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched",
        ),
        completion_sentinel_evidence=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="declare_complete matched",
        ),
    )
    assert completion_signals_terminal(completed) is True


def test_graded_verdict_returns_degraded_when_sentinel_missing() -> None:
    """``graded_verdict`` reports the weakest provenance across required facts.

    A planning phase whose agent never wrote the completion sentinel has
    ``completion_sentinel_evidence.holds=False``; the graded verdict
    must reflect that the phase is incomplete, not print a transcript-
    echoed success.
    """
    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=True,
        artifact_types=("plan",),
        completion_sentinel_present=False,
        artifact_required=True,
        unsubmitted_draft_present=False,
        required_artifact_evidence=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched",
        ),
        completion_sentinel_evidence=absent("completion sentinel was not observed"),
    )

    label, weakest = graded_verdict(signals)

    assert label == "DEGRADED"
    assert weakest == Provenance.ABSENT


def test_graded_verdict_returns_degraded_for_host_synthesized_sentinel() -> None:
    """A host-synthesized sentinel (``HOST_SYNTHESIZED``) caps the verdict at ``DEGRADED``.

    Per F7 / DoD 19, the host no longer writes completion evidence for
    any transport; if a future regression reintroduces the synthesis,
    the graded verdict must still downgrade to ``DEGRADED
    (host-synthesized)`` rather than reading as ``PASS``.
    """
    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=True,
        artifact_types=("plan",),
        completion_sentinel_present=True,
        artifact_required=True,
        unsubmitted_draft_present=False,
        required_artifact_evidence=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched",
        ),
        completion_sentinel_evidence=Evidence(
            holds=True,
            provenance=Provenance.HOST_SYNTHESIZED,
            detail="written by the harness",
        ),
    )

    label, weakest = graded_verdict(signals)

    assert label == "DEGRADED"
    assert weakest == Provenance.HOST_SYNTHESIZED


def test_graded_verdict_returns_pass_for_full_wire() -> None:
    """All-WIRE evidence grades ``PASS``.

    Required fact (``artifact_submitted`` or equivalent): holds +
    ``WIRE``. Sentinel: holds + ``WIRE``. The plan's pure-function
    rule: a run with every required fact at ``WIRE`` grades ``PASS``.
    """
    signals = CompletionSignals(
        explicit_complete=True,
        required_artifact_present=True,
        artifact_types=("plan",),
        completion_sentinel_present=True,
        artifact_required=True,
        unsubmitted_draft_present=False,
        required_artifact_evidence=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched",
        ),
        completion_sentinel_evidence=Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="declare_complete matched",
        ),
    )

    label, weakest = graded_verdict(signals)

    assert label == "PASS"
    assert weakest == Provenance.WIRE


def test_graded_completion_signals_returns_evidence_dict() -> None:
    """``graded_completion_signals`` extracts the evidence dict for grading.

    The grading pipeline (``format_verdict``) needs a ``Mapping[str,
    Evidence]``; the helper just slices the required facts off the
    signals dataclass. Used by S-5 to render the operator-facing
    verdict line.
    """
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
        required_artifact_present=True,
        artifact_types=("plan",),
        completion_sentinel_present=True,
        artifact_required=True,
        unsubmitted_draft_present=False,
        required_artifact_evidence=artifact_evidence,
        completion_sentinel_evidence=sentinel_evidence,
    )

    evidence = graded_completion_signals(signals)

    assert evidence == {
        "required_artifact_present": artifact_evidence,
        "completion_sentinel_present": sentinel_evidence,
    }


def test_smoke_provenance_matches_smoke_evidence_provenance() -> None:
    """The Provenance imported by completion_signals is the same enum as smoke_evidence's.

    Pin this so a future re-export shuffle does not silently split the
    lattice into two enums with the same members but different identity.
    """
    assert Provenance is SmokeProvenance
