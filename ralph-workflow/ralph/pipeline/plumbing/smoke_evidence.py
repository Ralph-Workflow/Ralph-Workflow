
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
    return Evidence(holds=False, provenance=Provenance.ABSENT, detail=detail)


PASS = "PASS"
DEGRADED = "DEGRADED"


def grade_verdict(evidence: Mapping[str, Evidence]) -> tuple[str, Provenance]:
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
