"""Evidence Provenance: the trust lattice for smoke-gate contract facts.

Every contract fact the interactive smoke gate reports (``artifact_submitted``,
``explicit_completion_seen``, ``tool_activity_seen``) used to be a bare
``bool``. A ``True`` earned by a real ``tools/call`` on Ralph's MCP server and
a ``True`` the harness wrote to itself were indistinguishable, so the gate
could print ``Breaks: none`` for a run that never reached the transport it
claimed to be testing.

This module ships the fix: no fact reaches the report without saying where it
came from, and the verdict is derived from the weakest provenance backing any
required fact. See ``.agent/PRODUCT_CRITERIA.md`` (Evidence Provenance / F1)
for the product brief this implements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.server._wire_ledger import wire_evidence_for
from ralph.pipeline.plumbing.smoke_provenance import Provenance

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = [
    "DEGRADED",
    "PASS",
    "Evidence",
    "Provenance",
    "absent",
    "format_verdict",
    "grade_artifact_submission_evidence",
    "grade_completion_sentinel_evidence",
    "grade_verdict",
]


@dataclass(frozen=True)
class Evidence:
    """One graded fact: whether it holds, how confidently, and why.

    ``Evidence`` cannot be constructed without a ``Provenance`` — the type
    system, not code review, is what stops a future contributor from
    reintroducing a bare ``bool``. A holding fact can never carry
    ``Provenance.ABSENT``: "it happened" and "there is no evidence it
    happened" are mutually exclusive by construction.
    """

    holds: bool
    provenance: Provenance
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, Provenance):
            raise TypeError(
                f"Evidence.provenance must be a Provenance member, got {self.provenance!r}"
            )
        if self.holds and self.provenance is Provenance.ABSENT:
            raise ValueError(
                "Evidence.holds=True cannot carry Provenance.ABSENT "
                f"(detail={self.detail!r}) — a holding fact must cite some evidence"
            )


def absent(detail: str) -> Evidence:
    """Return the canonical "this fact does not hold" Evidence value."""
    return Evidence(holds=False, provenance=Provenance.ABSENT, detail=detail)


PASS = "PASS"
DEGRADED = "DEGRADED"


def grade_verdict(evidence: Mapping[str, Evidence]) -> tuple[str, Provenance]:
    """Return ``(verdict_label, weakest_provenance)`` for a set of required facts.

    ``PASS`` requires every fact in ``evidence`` to both hold and be graded
    ``Provenance.WIRE``. Any fact below that bar — including one that simply
    does not hold — demotes the verdict to ``DEGRADED``, reported with the
    single weakest provenance among *all* facts (not just the failing ones),
    matching the brief's rule that the verdict is a pure function of the
    weakest provenance backing any required fact.

    An empty ``evidence`` mapping has no required fact to back a ``PASS`` and
    grades ``DEGRADED`` at ``Provenance.ABSENT`` — there is nothing to
    demonstrate trust with.
    """
    if not evidence:
        return DEGRADED, Provenance.ABSENT
    weakest = min(ev.provenance for ev in evidence.values())
    all_wire_and_holding = all(
        ev.holds and ev.provenance is Provenance.WIRE for ev in evidence.values()
    )
    if all_wire_and_holding:
        return PASS, Provenance.WIRE
    return DEGRADED, weakest


def format_verdict(evidence: Mapping[str, Evidence]) -> str:
    """Return the operator-facing verdict string, e.g. ``DEGRADED (host-synthesized)``."""
    label, weakest = grade_verdict(evidence)
    if label == PASS:
        return PASS
    return f"{DEGRADED} ({weakest.name.lower().replace('_', '-')})"


def grade_artifact_submission_evidence(
    workspace_root: Path,
    run_id: str,
    *,
    submitted: bool,
    secret: str | None,
) -> Evidence:
    """Grade an artifact-submission fact: fallback promotion vs. a wire hit.

    ``submitted`` is the pre-computed authoritative bool (a receipt exists,
    possibly after promoting a fallback document through the canonical submit
    path). A submission backed by a matching ``tools/call`` ledger record
    grades ``WIRE``; any other submitted receipt (including one promoted from
    the model's fallback markdown file) grades ``WORKSPACE_EFFECT`` — real,
    but not attributable to a witnessed tool call.

    Shared by the smoke gate and ``ralph.agents.completion_signals.
    evaluate_completion`` so both grade a phase's required-artifact fact the
    same way, instead of maintaining the WIRE-grading decision twice.
    """
    if not submitted:
        return absent("smoke_test_result artifact was not submitted")
    if wire_evidence_for(workspace_root, run_id, tool_name="artifact", secret=secret):
        return Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="receipt matched a tools/call ledger record",
        )
    return Evidence(
        holds=True,
        provenance=Provenance.WORKSPACE_EFFECT,
        detail="receipt present (direct submission or promoted fallback); no matching wire-ledger record",
    )


def grade_completion_sentinel_evidence(
    workspace_root: Path,
    run_id: str,
    *,
    present: bool,
    host_synthesized: bool,
    secret: str | None,
) -> Evidence:
    """Grade a completion-sentinel fact.

    A sentinel the harness wrote to itself (a host-synthesis fallback)
    grades ``HOST_SYNTHESIZED`` — it caps the run's verdict at ``DEGRADED``
    and names itself, rather than reading as unqualified proof the agent
    called ``declare_complete``. An unsigned sentinel (no broker secret in
    scope) is capped at ``TRANSCRIPT``: "not a weaker WIRE fact — not a WIRE
    fact." Only a sentinel backed by a matching ``declare_complete``
    wire-ledger record grades ``WIRE``.

    Shared by the smoke gate and ``ralph.agents.completion_signals.
    evaluate_completion`` so both grade a phase's completion-sentinel fact
    the same way, instead of maintaining the WIRE-grading decision twice.
    """
    if not present:
        return absent("completion sentinel was not observed")
    if host_synthesized:
        return Evidence(
            holds=True,
            provenance=Provenance.HOST_SYNTHESIZED,
            detail="written by the harness (fallback-artifact completion synthesis)",
        )
    if secret is None:
        return Evidence(
            holds=True,
            provenance=Provenance.TRANSCRIPT,
            detail="sentinel present but RALPH_BROKER_SECRET is unset; HMAC unverified, not WIRE",
        )
    if wire_evidence_for(workspace_root, run_id, tool_name="declare_complete", secret=secret):
        return Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="declare_complete matched a tools/call ledger record",
        )
    return Evidence(
        holds=True,
        provenance=Provenance.TRANSCRIPT,
        detail="sentinel present but no matching wire-ledger record",
    )
