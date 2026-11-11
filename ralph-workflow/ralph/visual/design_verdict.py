
from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field

from ralph.visual.capture_set import CaptureSet
from ralph.visual.policy_facts import REQUIRED_STATES
from ralph.visual.visual_finding import (
    VISUAL_SEVERITIES,
    VisualFinding,
)


VERDICT_PASS: str = "pass"
VERDICT_FAIL: str = "fail"
VERDICT_BLOCKED: str = "blocked"

VERDICT_VALUES: tuple[str, ...] = (VERDICT_PASS, VERDICT_FAIL, VERDICT_BLOCKED)

_ABSOLUTE_CLAIM_FRAGMENTS: tuple[str, ...] = (
    "should be ",
    "must be ",
    "needs to be ",
    "expected to be ",
    "is not aligned",
    "is not visible",
    "is not legible",
    "is not accessible",
)

_BEFORE_GROUNDING_HINTS: tuple[str, ...] = (
    "compared to before",
    "compared to baseline",
    "differs from before",
    "differs from baseline",
    "regressed from",
    "no longer",
    "no change compared to before",
    "matches before",
    "now matches",
    "now differs",
)

_INPUT_SMUGGLE_FRAGMENTS: tuple[str, ...] = (
    "source code",
    "source diff",
    "the diff",
    "diff hunks",
    "dom snapshot",
    "dom tree",
    "stylesheet contents",
    "css source",
)

_INTENT_MAX_LEN: int = 16 * 1024


def _fixture_capture_bytes(capture: Mapping[str, object], label: str) -> tuple[bytes, ...]:
    cells = capture.get("cells")
    if not isinstance(cells, list) or not cells:
        raise ValueError(f"{label} capture evidence is required")
    evidence: list[bytes] = []
    for cell in cells:
        if not isinstance(cell, Mapping):
            raise ValueError(f"{label} capture evidence is required")
        encoded = cell.get("evidence_b64")
        if not isinstance(encoded, str):
            raise ValueError(f"{label} capture evidence is required")
        try:
            evidence.append(base64.b64decode(encoded, validate=True))
        except binascii.Error as exc:
            raise ValueError(f"{label} capture evidence must be base64") from exc
    return tuple(evidence)


def validate_deterministic_capture_evidence(
    before: Mapping[str, object],
    after: Mapping[str, object],
    verdict: Mapping[str, object],
) -> None:
    handles = verdict.get("cell_handles")
    if not isinstance(handles, list) or not handles:
        raise ValueError("capture handles are required")
    before_bytes = _fixture_capture_bytes(before, "before")
    after_bytes = _fixture_capture_bytes(after, "after")
    if verdict.get("visual_verdict") == "improved" and before_bytes == after_bytes:
        raise ValueError("an improved verdict requires non-byte-identical before/after evidence")




def _validate_inputs_typed(
    *,
    before: object,
    after: object,
    intent: object,
    status: object,
    findings: object,
) -> str:
    if not isinstance(before, CaptureSet):
        raise ValueError("DesignVerdict.before must be a CaptureSet")
    if not isinstance(after, CaptureSet):
        raise ValueError("DesignVerdict.after must be a CaptureSet")
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("DesignVerdict.intent must be a non-empty string")
    if len(intent) > _INTENT_MAX_LEN:
        raise ValueError(
            f"DesignVerdict.intent length {len(intent)} exceeds _INTENT_MAX_LEN={_INTENT_MAX_LEN}"
        )
    if not isinstance(status, str) or status not in VERDICT_VALUES:
        raise ValueError(
            f"DesignVerdict.status must be one of {list(VERDICT_VALUES)}; got {status!r}"
        )
    if not isinstance(findings, tuple):
        raise ValueError("DesignVerdict.findings must be a tuple of VisualFinding instances")
    return status


def _validate_intent_no_smuggle(intent: str) -> None:
    lowered = intent.lower()
    smuggled = [fragment for fragment in _INPUT_SMUGGLE_FRAGMENTS if fragment in lowered]
    if smuggled:
        raise ValueError(
            "DesignVerdict.intent must not smuggle non-capture artifacts "
            f"(rejected phrases: {smuggled!r}); verdicts take before + after + intent only"
        )


def _validate_targets_match(before: CaptureSet, after: CaptureSet) -> None:
    if before.target != after.target:
        raise ValueError(
            f"DesignVerdict target mismatch: before.target={before.target!r} "
            f"but after.target={after.target!r}"
        )


def _validate_matrix_parity(
    *,
    before_ids: frozenset[str],
    after_ids: frozenset[str],
) -> None:
    if before_ids == after_ids:
        return
    missing_from_after = sorted(before_ids - after_ids)
    extra_in_after = sorted(after_ids - before_ids)
    problems: list[str] = []
    if missing_from_after:
        problems.append(
            f"after is missing {len(missing_from_after)} baseline cells "
            f"(e.g. {missing_from_after[:3]!r})"
        )
    if extra_in_after:
        problems.append(
            f"after introduces {len(extra_in_after)} cells not in baseline "
            f"(e.g. {extra_in_after[:3]!r})"
        )
    raise ValueError(
        "DesignVerdict.before and DesignVerdict.after must describe the "
        "same capture matrix (same cells, same id set); "
        f"{'; '.join(problems)} — substituted baselines are rejected"
    )


def _validate_states_covered(
    *,
    before: CaptureSet,
    after: CaptureSet,
) -> None:
    before_states = before.states_covered()
    after_states = after.states_covered()
    missing_before = [s for s in REQUIRED_STATES if s not in before_states]
    if missing_before:
        raise ValueError(
            f"DesignVerdict.before is missing required states "
            f"{missing_before!r}; single-screenshot baselines are rejected"
        )
    missing_after = [s for s in REQUIRED_STATES if s not in after_states]
    if missing_after:
        raise ValueError(
            f"DesignVerdict.after is missing required states "
            f"{missing_after!r}; an incomplete after-capture cannot "
            "support a verdict"
        )


def _validate_findings(
    *,
    findings: tuple[VisualFinding, ...],
    valid_capture_ids: frozenset[str],
) -> dict[str, tuple[VisualFinding, ...]]:
    by_capture: dict[str, list[VisualFinding]] = {}
    for finding in findings:
        if not isinstance(finding, VisualFinding):
            raise ValueError("DesignVerdict.findings must contain only VisualFinding instances")
        if finding.capture_id not in valid_capture_ids:
            raise ValueError(
                f"VisualFinding cites capture_id={finding.capture_id!r} which "
                "is not in the shared capture matrix; verdicts cannot reference "
                "captures that were never produced"
            )
        _validate_finding_grounding(finding)
        by_capture.setdefault(finding.capture_id, []).append(finding)
    return {capture_id: tuple(items) for capture_id, items in by_capture.items()}


def _validate_finding_grounding(finding: VisualFinding) -> None:
    if finding.severity not in VISUAL_SEVERITIES:
        raise ValueError(
            f"VisualFinding has invalid severity {finding.severity!r}; "
            f"expected one of {list(VISUAL_SEVERITIES)}"
        )
    narrative_lower = finding.narrative.lower()
    has_absolute_claim = any(fragment in narrative_lower for fragment in _ABSOLUTE_CLAIM_FRAGMENTS)
    if not has_absolute_claim:
        return
    has_grounding = any(hint in narrative_lower for hint in _BEFORE_GROUNDING_HINTS)
    if not has_grounding:
        raise ValueError(
            "VisualFinding narrative asserts an absolute claim without a "
            "before-grounding phrase (e.g. 'compared to before', "
            "'differs from baseline'); verdicts cannot evaluate "
            "unfalsifiable absolute claims. Narrative was: "
            f"{finding.narrative!r}"
        )


def _validate_status_consistency(status: str, findings: tuple[VisualFinding, ...]) -> None:
    blocker_count = sum(1 for f in findings if f.severity == "blocker")
    major_count = sum(1 for f in findings if f.severity == "major")
    if status == VERDICT_PASS and (blocker_count or major_count):
        offenders = [f.severity for f in findings if f.severity in {"blocker", "major"}]
        raise ValueError(
            f"DesignVerdict.status='pass' is inconsistent with "
            f"{len(offenders)} blocker/major findings ({offenders}); "
            "either remove the findings or set status='fail'"
        )
    if status == VERDICT_FAIL and blocker_count == 0 and major_count == 0:
        raise ValueError(
            "DesignVerdict.status='fail' requires at least one blocker "
            "or major finding; downgrade to 'pass' or add a finding"
        )




@dataclass(frozen=True, slots=True, eq=False)
class DesignVerdict:

    before: CaptureSet
    after: CaptureSet
    intent: str
    status: str
    findings: tuple[VisualFinding, ...]
    _by_capture: dict[str, tuple[VisualFinding, ...]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        status = _validate_inputs_typed(
            before=self.before,
            after=self.after,
            intent=self.intent,
            status=self.status,
            findings=self.findings,
        )
        _validate_intent_no_smuggle(self.intent)
        _validate_targets_match(self.before, self.after)
        _validate_matrix_parity(
            before_ids=self.before.cell_ids,
            after_ids=self.after.cell_ids,
        )
        _validate_states_covered(before=self.before, after=self.after)
        _validate_status_consistency(status, self.findings)

        grouped = _validate_findings(
            findings=self.findings,
            valid_capture_ids=self.before.cell_ids,
        )
        object.__setattr__(self, "_by_capture", grouped)


    @property
    def cell_ids(self) -> frozenset[str]:
        return self.before.cell_ids

    def findings_for(self, capture_id: str) -> tuple[VisualFinding, ...]:
        return self._by_capture.get(capture_id, ())

    def blockers(self) -> tuple[VisualFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "blocker")

    def majors(self) -> tuple[VisualFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "major")


    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:
        return id(self)


__all__ = [
    "VERDICT_BLOCKED",
    "VERDICT_FAIL",
    "VERDICT_PASS",
    "VERDICT_VALUES",
    "DesignVerdict",
    "validate_deterministic_capture_evidence",
]
