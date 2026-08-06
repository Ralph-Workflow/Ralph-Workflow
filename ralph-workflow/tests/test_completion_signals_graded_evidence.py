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

    Post-S-4, ``required_artifact_present`` and
    ``completion_sentinel_present`` are required ``Evidence`` values on
    the dataclass -- the contract fields themselves are ``Evidence``-typed,
    not parallel ``bool`` fields that merely mirror graded ``Evidence``
    siblings. The type system forbids re-introducing a bare ``bool`` as
    the contract field for new code.
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
        required_artifact_present=artifact_evidence,
        artifact_types=("plan",),
        completion_sentinel_present=sentinel_evidence,
        artifact_required=True,
        unsubmitted_draft_present=False,
    )
    assert signals.required_artifact_present is artifact_evidence
    assert signals.completion_sentinel_present is sentinel_evidence
    # The alias fields mirror the contract fields by default.
    assert signals.required_artifact_evidence is artifact_evidence
    assert signals.completion_sentinel_evidence is sentinel_evidence


def test_completion_signals_terminal_continues_to_gate_on_holds() -> None:
    """``completion_signals_terminal`` gates on ``.holds`` of the Evidence-typed fields.

    S-4: the contract fields are Evidence-typed and the terminal gate
    reads ``.holds``. The semantics match the pre-S-4 bool contract
    (sentinel must hold; artifact must hold when artifact_required is
    True), but the underlying type is Evidence so a future contributor
    cannot quietly revert to a bare bool.
    """
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
    """S-4 regression: absent completion-sentinel Evidence forces terminal=False.

    Even when ``explicit_complete`` is True (the transcript marker
    matched) and the artifact receipt is present, an absent
    ``completion_sentinel_present`` Evidence (no sentinel on disk,
    no HMAC match, no declared completion) MUST keep the phase
    non-terminal. This is the load-bearing guard against the
    2026-08-06 planning run printing ``agy result SUCCESS`` -- the
    transcript claimed success, no sentinel existed, and the phase
    went on to ``auto-integrate skipped``.
    """
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
    """S-4 regression: absent artifact Evidence forces terminal=False when artifact_required.

    A required-artifact phase whose receipt Evidence is absent
    (the agent never submitted the artifact) must not report
    terminal completion, regardless of the sentinel's status. This
    is the F6 / DoD 12 invariant: "a phase that produced no
    artifact must not report success".
    """
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
    """The terminal gate is binary: a holding fact below WIRE still terminates.

    ``completion_signals_terminal`` is the *binary* completion gate,
    not the trust-grading gate. A holding Evidence graded
    ``WORKSPACE_EFFECT`` (a receipt stamped by the canonical-submit
    path) still terminates the phase -- the trust-grading lives in
    ``graded_verdict`` / ``format_phase_verdict``. This pins the
    separation: completion ≠ trust.
    """
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
    """``graded_verdict`` reports the weakest provenance across required facts.

    A planning phase whose agent never wrote the completion sentinel has
    ``completion_sentinel_present.holds=False``; the graded verdict
    must reflect that the phase is incomplete, not print a transcript-
    echoed success.
    """
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
    """A host-synthesized sentinel (``HOST_SYNTHESIZED``) caps the verdict at ``DEGRADED``.

    Per F7 / DoD 19, the host no longer writes completion evidence for
    any transport; if a future regression reintroduces the synthesis,
    the graded verdict must still downgrade to ``DEGRADED
    (host-synthesized)`` rather than reading as ``PASS``.
    """
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
    """All-WIRE evidence grades ``PASS``.

    Required fact (``artifact_submitted`` or equivalent): holds +
    ``WIRE``. Sentinel: holds + ``WIRE``. The plan's pure-function
    rule: a run with every required fact at ``WIRE`` grades ``PASS``.
    """
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
    """The two contract fields are themselves ``Evidence`` typed (S-4 invariant).

    A bare ``bool`` cannot construct the ``required_artifact_present``
    or ``completion_sentinel_present`` field. ``__post_init__`` would
    coerce a legacy ``bool`` to ``Evidence`` at ``WORKSPACE_EFFECT``
    provenance for backward compat, so this test reads the post-init
    attribute directly to confirm the contract: the field carries an
    ``Evidence`` value after construction, never a bare ``bool``.
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
    """The Provenance imported by completion_signals is the same enum as smoke_evidence's.

    Pin this so a future re-export shuffle does not silently split the
    lattice into two enums with the same members but different identity.
    """
    assert Provenance is SmokeProvenance
