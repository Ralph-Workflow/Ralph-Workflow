"""DesignVerdict: the signed-off artifact for a visual change review.

A :class:`DesignVerdict` takes EXACTLY three inputs:

* ``before`` — :class:`~ralph.visual.capture_set.CaptureSet` (run-owned baseline)
* ``after``  — :class:`~ralph.visual.capture_set.CaptureSet` (run-owned re-capture)
* ``intent`` — ``str`` (the plan item text plus any repo declarations)

Anything else — diff, DOM, stylesheet, source code, single-screenshot
ground truth — is explicitly NOT a verdict input. The verdict's job is
to compare two complete capture matrices and emit ``pass`` / ``fail`` /
``blocked`` along with the findings that explain the verdict, each
citing a ``capture_id`` and a :class:`~ralph.visual.visual_finding.Region`.

Validation rejects:

* missing or substituted baselines (the ``before`` matrix must be
  present and must equal the ``after`` matrix),
* inputs that smuggle in ``source``/``diff``/``DOM``/``stylesheet``
  references in the intent narrative (the verdict is purely a
  before/after visual artifact; downstream layers are responsible for
  producing any other review),
* absolute claims whose narrative names a target state (``"should
  be"``, ``"must be"``, ``"needs to be"``, ``"expected to be"``,
  ``"is not aligned"``, ``"is not visible"``) without a
  ``"compared to before"``-style grounding phrase — those claims
  cannot be falsified by re-running the capture.
"""

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

# ---------------------------------------------------------------------------
# Verdict status vocabulary
# ---------------------------------------------------------------------------

VERDICT_PASS: str = "pass"
VERDICT_FAIL: str = "fail"
VERDICT_BLOCKED: str = "blocked"

# Closed verdict vocabulary. Anything outside this set is rejected at
# construction time so downstream consumers can group verdicts by
# status without spelling drift.
VERDICT_VALUES: tuple[str, ...] = (VERDICT_PASS, VERDICT_FAIL, VERDICT_BLOCKED)

# Phrases that signal an absolute claim not grounded in a
# before/after comparison. If a finding's narrative contains any of
# these without a grounding phrase, the verdict rejects the finding at
# construction time.
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

# Grounding phrases that license an absolute claim. The agent MUST
# explicitly cite the before capture for any absolute assertion —
# silent "I noticed X" claims cannot be re-verified.
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

# Substrings that signal a verdict input smuggling in a non-capture
# artifact. ``source``, ``diff``, ``DOM``, and ``stylesheet`` are
# explicitly NOT visual-verdict inputs — they belong to other
# reviewers (lint, type, accessibility). The intent narrative MUST
# stick to plan-item text and repo declarations.
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
    """Read fixture capture bytes for deterministic smoke validation only."""
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
    """Reject deterministic verdicts without captures or changed fixture bytes.

    This checks evidence transport only. It deliberately makes no visual-quality
    or taste judgement.
    """
    handles = verdict.get("cell_handles")
    if not isinstance(handles, list) or not handles:
        raise ValueError("capture handles are required")
    before_bytes = _fixture_capture_bytes(before, "before")
    after_bytes = _fixture_capture_bytes(after, "after")
    if verdict.get("visual_verdict") == "improved" and before_bytes == after_bytes:
        raise ValueError("an improved verdict requires non-byte-identical before/after evidence")


# ---------------------------------------------------------------------------
# Validation helpers — split out of __post_init__ to keep the dataclass
# entry point short and let ruff's PLR0912 (too-many-branches) gate
# pass without weakening lint enforcement.
# ---------------------------------------------------------------------------


def _validate_inputs_typed(
    *, before: object, after: object, intent: object,
    status: object, findings: object,
) -> str:
    """Type-check the three-input contract. Returns the validated status."""
    if not isinstance(before, CaptureSet):
        raise ValueError("DesignVerdict.before must be a CaptureSet")
    if not isinstance(after, CaptureSet):
        raise ValueError("DesignVerdict.after must be a CaptureSet")
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("DesignVerdict.intent must be a non-empty string")
    if len(intent) > _INTENT_MAX_LEN:
        raise ValueError(
            f"DesignVerdict.intent length {len(intent)} exceeds "
            f"_INTENT_MAX_LEN={_INTENT_MAX_LEN}"
        )
    if not isinstance(status, str) or status not in VERDICT_VALUES:
        raise ValueError(
            f"DesignVerdict.status must be one of {list(VERDICT_VALUES)}; "
            f"got {status!r}"
        )
    if not isinstance(findings, tuple):
        raise ValueError(
            "DesignVerdict.findings must be a tuple of VisualFinding instances"
        )
    return status


def _validate_intent_no_smuggle(intent: str) -> None:
    """Reject intent narratives that smuggle in non-capture artifacts."""
    lowered = intent.lower()
    smuggled = [
        fragment for fragment in _INPUT_SMUGGLE_FRAGMENTS if fragment in lowered
    ]
    if smuggled:
        raise ValueError(
            "DesignVerdict.intent must not smuggle non-capture artifacts "
            f"(rejected phrases: {smuggled!r}); verdicts take before + after + intent only"
        )


def _validate_targets_match(before: CaptureSet, after: CaptureSet) -> None:
    """Reject verdict inputs that compare two different targets."""
    if before.target != after.target:
        raise ValueError(
            f"DesignVerdict target mismatch: before.target={before.target!r} "
            f"but after.target={after.target!r}"
        )


def _validate_matrix_parity(
    *, before_ids: frozenset[str], after_ids: frozenset[str],
) -> None:
    """Reject before/after capture matrices that are not cell-id-equal."""
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
    *, before: CaptureSet, after: CaptureSet,
) -> None:
    """Reject baselines or after-captures that miss canonical states."""
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
    """Type-check findings, cite valid capture_ids, and group by capture."""
    by_capture: dict[str, list[VisualFinding]] = {}
    for finding in findings:
        if not isinstance(finding, VisualFinding):
            raise ValueError(
                "DesignVerdict.findings must contain only VisualFinding instances"
            )
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
    """Reject a finding whose narrative asserts an absolute state without before-grounding."""
    if finding.severity not in VISUAL_SEVERITIES:
        # Defensive: the VisualFinding constructor already enforces
        # this, but keep the check here so a future refactor of
        # VisualFinding cannot silently disable verdict grounding.
        raise ValueError(
            f"VisualFinding has invalid severity {finding.severity!r}; "
            f"expected one of {list(VISUAL_SEVERITIES)}"
        )
    narrative_lower = finding.narrative.lower()
    has_absolute_claim = any(
        fragment in narrative_lower for fragment in _ABSOLUTE_CLAIM_FRAGMENTS
    )
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


def _validate_status_consistency(
    status: str, findings: tuple[VisualFinding, ...]
) -> None:
    """Reject inconsistent status/findings pairings.

    * ``pass`` must have no blocker/major findings.
    * ``fail`` must have at least one blocker or major.
    * ``blocked`` is allowed with any finding set; the verdict
      is reserved for downstream "cannot evaluate" cases such as a
      missing baseline (already raised above) or an agent-side
      failure surfaced separately.
    """
    blocker_count = sum(1 for f in findings if f.severity == "blocker")
    major_count = sum(1 for f in findings if f.severity == "major")
    if status == VERDICT_PASS and (blocker_count or major_count):
        offenders = [
            f.severity for f in findings if f.severity in {"blocker", "major"}
        ]
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
    # ``blocked`` is permissive by design.


# ---------------------------------------------------------------------------
# Verdict object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class DesignVerdict:
    """A signed-off visual verdict comparing two complete capture matrices."""

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
        # ``object.__setattr__`` is required because the dataclass is
        # frozen; we cannot use ``self._by_capture = ...`` directly.
        # The dict+tuple types are explicit so the assignment does
        # not leak ``Any`` from mypy's view of ``object.__setattr__``.
        object.__setattr__(self, "_by_capture", grouped)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def cell_ids(self) -> frozenset[str]:
        """Return the shared cell-id set (before == after)."""
        return self.before.cell_ids

    def findings_for(self, capture_id: str) -> tuple[VisualFinding, ...]:
        """Return the findings that cite the given capture_id, in declaration order."""
        return self._by_capture.get(capture_id, ())

    def blockers(self) -> tuple[VisualFinding, ...]:
        """Return every blocker finding (severity == 'blocker')."""
        return tuple(f for f in self.findings if f.severity == "blocker")

    def majors(self) -> tuple[VisualFinding, ...]:
        """Return every major finding (severity == 'major')."""
        return tuple(f for f in self.findings if f.severity == "major")

    # ------------------------------------------------------------------
    # Identity-keyed equality (parity with CaptureSet)
    # ------------------------------------------------------------------

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
