"""Plan-artifact size limits and pure-return size guard.

This module is the single source of truth for the generous-but-bounded
size limits that apply to a plan artifact. ``PlanSizeLimits.DEFAULT``
defines every cap used by the plan schema, the runtime size guard, the
format doc, and the planning prompt templates.
No model hard-codes a cap.

The runtime size guard ``check_plan_size`` is a PURE helper that NEVER
raises. It returns the FIRST violation as a :class:`PlanArtifactSizeError`
(with ``.field``, ``.actual``, ``.cap`` attributes populated) or ``None``
if all caps are satisfied. The caller
(``normalize_plan_artifact_content`` in ``_validation.py``) is the
single point that raises ``PlanArtifactValidationError`` when the helper
returns a non-``None`` error.

Per-field cap table (kept in lockstep with the ``Field(..., max_length=...)``
constraints in the per-section modules):

| field | current cap | tier (short|medium|long) | new cap |
| --- | --- | --- | --- |
| ``PlanStep.title`` | (none) | short | 500 |
| ``PlanStep.content`` | (none) | long | 20000 |
| ``PlanStep.rationale`` | (none) | medium | 8000 |
| ``PlanStep.location`` | (none) | short | 500 |
| ``PlanStep.verify_command`` | (none) | medium | 2000 |
| ``PlanStep.targets`` (list) | (none) | short | 100 |
| ``PlanStep.depends_on`` (list) | (none) | short | 50 |
| ``PlanStep.satisfies`` (list) | (none) | short | 50 |
| ``StepTarget.path`` | (none) | short | 1000 |
| ``Summary.context`` | 2000 | medium | 8000 |
| ``Summary.intent`` | 200 | short | 500 |
| ``Summary.scope_items`` (list) | (none) | short | 200 |
| ``Summary.coverage_areas`` (list) | (none) | short | 50 |
| ``ScopeItem.text`` | (none) | short | 1000 |
| ``ScopeItem.count`` | (none) | short | 200 |
| ``PlanConstraints.performance_budget`` | 200 | medium | 2000 |
| ``PlanConstraints.security_posture`` | 200 | medium | 2000 |
| ``PlanConstraints.must_not_break[*]`` | 200 | short | 1000 |
| ``PlanConstraints.must_keep_working[*]`` | 200 | short | 1000 |
| ``SkillsMcp.skills`` (list) | (none) | short | 100 |
| ``SkillsMcp.mcps`` (list) | (none) | short | 50 |
| ``RiskMitigation.risk`` | (none) | medium | 8000 |
| ``RiskMitigation.mitigation`` | (none) | medium | 8000 |
| ``RiskMitigation.risks_mitigations`` (list) | (none) | short | 200 |
| ``CriticalPrimaryFile.path`` | (none) | short | 1000 |
| ``CriticalPrimaryFile.estimated_changes`` | (none) | short | 500 |
| ``ReferenceFile.path`` | (none) | short | 1000 |
| ``ReferenceFile.purpose`` | (none) | medium | 2000 |
| ``VerificationStep.method`` | (none) | medium | 2000 |
| ``VerificationStep.expected_outcome`` | (none) | medium | 8000 |
| ``VerificationStep.cwd`` | 200 | short | 500 |
| ``AcceptanceCriterion.description`` | (none) | medium | 8000 |
| ``AcceptanceCriterion.verification_step`` | (none) | medium | 2000 |
| ``AcceptanceCriterion.evidence_path`` | (none) | short | 1000 |
| ``AcceptanceCriterion.satisfied_by_steps`` (list) | (none) | short | 50 |
| ``EvidenceRef.ref`` | 200 | short | 1000 |
| ``EvidenceRef.note`` | 200 | short | 1000 |
| ``DesignSection.outcome`` | 500 | short | 1000 |
| ``DesignSection.notes`` | (none) | long | 20000 |
| ``DesignConstraints.text`` | 2000 | long | 10000 |
| ``DesignConstraints.invariants[*]`` | (none) | medium | 2000 |
| ``NonGoals.items[*]`` | 500 | medium | 2000 |
| ``DependencyInjection.notes`` | (none) | medium | 8000 |
| ``DependencyInjection.preferred_patterns`` (list) | (none) | short | 20 |
| ``DependencyInjection.forbidden_patterns`` (list) | (none) | short | 50 |
| ``DriftDetection.guard_commands[*]`` | (none) | short | 500 |
| ``DriftDetection.expected_outputs[*]`` | (none) | medium | 2000 |
| ``DriftDetection.sources`` (list) | (none) | short | 20 |
| ``Testability.forbidden_in_tests`` (list) | (none) | short | 50 |
| ``Testability.required_test_layers`` (list) | (none) | short | 20 |
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import ClassVar

_MAX_TOTAL_BYTES_CEILING = 64_000_000


@dataclass(frozen=True)
class PlanSizeLimits:
    """Generous-but-bounded size limits for a plan artifact.

    Every named cap is a positive integer (except ``max_total_bytes``,
    which must additionally be ``<= 64_000_000`` so a future contributor
    cannot accidentally zero the cap or set a typo like ``10**12``).
    The three string-length tiers
    (``max_string_short`` <= ``max_string_medium`` <= ``max_string_long``)
    form a strictly non-decreasing sequence so the per-field cap table
    can be relied on.

    Construct a tighter limits object to scope a test
    (``PlanSizeLimits(max_total_bytes=1024)``) without monkey-patching
    the module-level ``DEFAULT`` instance.

    Invariant checks run in ``__post_init__`` via ``if/raise RuntimeError``
    so they survive ``python -O``.
    """

    max_total_bytes: int = 4_000_000
    max_steps: int = 500
    max_scope_items: int = 200
    max_acceptance_criteria: int = 500
    max_evidence_per_step: int = 500
    max_risks: int = 200
    max_verification_steps: int = 100
    max_primary_files: int = 200
    max_reference_files: int = 200
    max_parallel_plan_items: int = 200
    max_work_units: int = 200
    max_constraint_list_entries: int = 500
    max_string_short: int = 1000
    max_string_medium: int = 8000
    max_string_long: int = 20000

    DEFAULT: ClassVar[PlanSizeLimits]

    def __post_init__(self) -> None:
        _validate_limits_invariants(self)


class PlanArtifactSizeError(ValueError):
    """Returned-by-value when a plan artifact exceeds its size cap.

    Carries three public attributes:

    - ``field``: dotted path to the offending field (e.g. ``"steps"``,
      ``"summary.scope_items"``, ``"total_bytes"``).
    - ``actual``: the offending value (list length or byte count).
    - ``cap``: the cap that was exceeded.

    The ``__str__`` representation is the canonical, agent-facing
    diagnostic message.
    """

    def __init__(self, field: str, actual: int | float, cap: int | float) -> None:
        self.field = field
        self.actual = actual
        self.cap = cap
        super().__init__(
            f"plan size violation: field={self.field!r} actual={self.actual} cap={self.cap}"
        )


def _validate_limits_invariants(limits: PlanSizeLimits) -> None:
    """Import-time invariant checks for ``PlanSizeLimits``.

    Mirrors the ``if``/``raise RuntimeError`` pattern used by other
    Ralph modules so the checks survive ``python -O``. The invariants
    defend against a future contributor silently zeroing the cap,
    inverting the tier order, or accepting a runaway cap value.
    """
    if limits.max_total_bytes <= 0:
        raise RuntimeError(
            f"PlanSizeLimits.max_total_bytes must be positive (got {limits.max_total_bytes})"
        )
    if limits.max_total_bytes > _MAX_TOTAL_BYTES_CEILING:
        raise RuntimeError(
            f"PlanSizeLimits.max_total_bytes must be <= {_MAX_TOTAL_BYTES_CEILING} "
            f"(got {limits.max_total_bytes})"
        )
    numeric_caps: tuple[tuple[str, int], ...] = (
        ("max_steps", limits.max_steps),
        ("max_scope_items", limits.max_scope_items),
        ("max_acceptance_criteria", limits.max_acceptance_criteria),
        ("max_evidence_per_step", limits.max_evidence_per_step),
        ("max_risks", limits.max_risks),
        ("max_verification_steps", limits.max_verification_steps),
        ("max_primary_files", limits.max_primary_files),
        ("max_reference_files", limits.max_reference_files),
        ("max_parallel_plan_items", limits.max_parallel_plan_items),
        ("max_work_units", limits.max_work_units),
        ("max_constraint_list_entries", limits.max_constraint_list_entries),
        ("max_string_short", limits.max_string_short),
        ("max_string_medium", limits.max_string_medium),
        ("max_string_long", limits.max_string_long),
    )
    for name, value in numeric_caps:
        if value <= 0:
            raise RuntimeError(f"PlanSizeLimits.{name} must be positive (got {value})")
    if not (limits.max_string_short <= limits.max_string_medium <= limits.max_string_long):
        raise RuntimeError(
            "PlanSizeLimits string tiers must form a non-decreasing sequence "
            f"(short={limits.max_string_short} <= "
            f"medium={limits.max_string_medium} <= "
            f"long={limits.max_string_long})"
        )


# Validate the dataclass defaults at import time; this raises
# RuntimeError immediately if any default value violates the invariants.
_validate_limits_invariants(PlanSizeLimits())

# Module-level DEFAULT instance used by callers that don't pass an explicit
# ``limits`` argument. Bound to ``PlanSizeLimits.DEFAULT`` as a class-level
# alias for ergonomic access from call sites and tests.
PLAN_SIZE_LIMITS: type[PlanSizeLimits] = PlanSizeLimits
default_instance = PlanSizeLimits()
PlanSizeLimits.DEFAULT = default_instance
_DEFAULT_INSTANCE: PlanSizeLimits = default_instance


# (path, cap) pairs checked in declaration order. The first violation wins.
# ``path`` is the dotted path the agent sees in the error message; the lookup
# is implemented inline so the helper stays pure and testable.
_LIST_CAP_CHECKS: tuple[tuple[str, int], ...] = (
    ("steps", _DEFAULT_INSTANCE.max_steps),
    ("summary.scope_items", _DEFAULT_INSTANCE.max_scope_items),
    (
        "design.acceptance_criteria.criteria",
        _DEFAULT_INSTANCE.max_acceptance_criteria,
    ),
    (
        "steps[*].expected_evidence",
        _DEFAULT_INSTANCE.max_evidence_per_step,
    ),
    ("risks_mitigations", _DEFAULT_INSTANCE.max_risks),
    ("verification_strategy", _DEFAULT_INSTANCE.max_verification_steps),
    ("critical_files.primary_files", _DEFAULT_INSTANCE.max_primary_files),
    ("critical_files.reference_files", _DEFAULT_INSTANCE.max_reference_files),
    ("parallel_plan", _DEFAULT_INSTANCE.max_parallel_plan_items),
    ("work_units", _DEFAULT_INSTANCE.max_work_units),
)


def _get_path(payload: dict[str, object], path: str) -> object:
    """Resolve a dotted path against a payload; returns ``None`` if any segment is absent."""
    node: object = payload
    for segment in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(segment)
        if node is None:
            return None
    return node


def _get_step_evidence_len(payload: dict[str, object]) -> int | None:
    """Return the maximum expected_evidence length across all steps, or None when steps absent."""
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return None
    max_len: int | None = None
    for step in steps:
        if not isinstance(step, dict):
            continue
        evidence = step.get("expected_evidence")
        if isinstance(evidence, list):
            length = len(evidence)
            if max_len is None or length > max_len:
                max_len = length
    return max_len


def _get_constraint_list_len(payload: dict[str, object], key: str) -> int | None:
    """Return the length of ``constraints[key]`` if it is a list, else None."""
    constraints = payload.get("constraints")
    if not isinstance(constraints, dict):
        return None
    value = constraints.get(key)
    if isinstance(value, list):
        return len(value)
    return None


def check_plan_size(
    content: object,
    *,
    limits: PlanSizeLimits = PlanSizeLimits.DEFAULT,
) -> PlanArtifactSizeError | None:
    """Return the first size violation, or ``None`` if the payload is within every cap.

    PURE helper — never raises. The caller (``normalize_plan_artifact_content``)
    is the single point that raises ``PlanArtifactValidationError`` when this
    function returns a non-``None`` error.

    Checks run in this order; the first violation wins:

    1. ``len(json.dumps(content, default=str)) > limits.max_total_bytes`` —
       the 4 MB hard byte cap. Runs FIRST so a runaway payload is rejected
       before per-list traversal.
    2. Per-list length checks: ``steps``, ``summary.scope_items``,
       ``design.acceptance_criteria.criteria``, ``steps[*].expected_evidence``,
       ``risks_mitigations``, ``verification_strategy``,
       ``critical_files.primary_files``, ``critical_files.reference_files``,
       ``parallel_plan``, ``work_units``.
    3. Per-constraint-list checks: ``constraints.must_not_break`` and
       ``constraints.must_keep_working``.
    """
    if not isinstance(content, dict):
        content_dict: dict[str, object] = {}
    else:
        content_dict = content

    serialized = json.dumps(content, default=str)
    if len(serialized) > limits.max_total_bytes:
        return PlanArtifactSizeError(
            field="total_bytes",
            actual=len(serialized),
            cap=limits.max_total_bytes,
        )

    for path, cap in _LIST_CAP_CHECKS:
        if path == "steps[*].expected_evidence":
            actual = _get_step_evidence_len(content_dict)
        else:
            actual_obj = _get_path(content_dict, path)
            actual = len(actual_obj) if isinstance(actual_obj, list) else None
        if actual is not None and actual > cap:
            return PlanArtifactSizeError(field=path, actual=actual, cap=cap)

    for key in ("must_not_break", "must_keep_working"):
        actual = _get_constraint_list_len(content_dict, key)
        if actual is not None and actual > limits.max_constraint_list_entries:
            return PlanArtifactSizeError(
                field=f"constraints.{key}",
                actual=actual,
                cap=limits.max_constraint_list_entries,
            )

    return None


__all__ = [
    "PLAN_SIZE_LIMITS",
    "PlanArtifactSizeError",
    "PlanSizeLimits",
    "check_plan_size",
]
