"""Plan-artifact validation, normalization, and payload decoding.

The single source of truth for ``PlanArtifact`` (the top-level
validated schema) lives here because it owns the cross-reference
validator (steps <-> acceptance-criteria) and needs the
``_collect_criteria`` / ``_check_satisfies_links`` /
``_check_satisfied_by_steps_links`` helpers in the same module.

The lazy WorkUnit forward reference (handled by
``_PlanArtifactRebuildState`` / ``_ensure_plan_artifact_rebuilt``)
also lives here so the rebuild state is a local concern of the
module that owns the model that needs it.

The payload decoding helpers (``parse_plan_payload_strict``,
``parse_plan_payload_lenient``, and the private
``_decode_plan_payload`` core) consolidate the four previously
duplicated JSON parsers that lived in ``tools/artifact.py``,
``prompts/plan_format.py``, and the original ``__init__.py``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib import import_module
from typing import TYPE_CHECKING, cast

from pydantic import ConfigDict, Field, ValidationError, model_validator

from ralph.mcp.artifacts.plan._section_models import (
    AcceptanceCriterion,
    CriticalFiles,
    DesignSection,
    PlanArtifactDict,
    PlanConstraints,
    PlanStep,
    RiskMitigation,
    ScopeItem,
    SkillsMcp,
    Summary,
    VerificationStep,
)
from ralph.mcp.artifacts.plan._section_registry import (
    PLAN_SECTION_LIST_ITEM_MODELS,
    PLAN_SECTION_NAMES,
    PLAN_SECTION_OBJECT_MODELS,
    SectionMode,
)
from ralph.mcp.artifacts.plan._size_limits import check_plan_size
from ralph.mcp.artifacts.plan._step_contract import StepType
from ralph.mcp.artifacts.plan.plan_artifact_validation_error import (
    PlanArtifactValidationError,
)
from ralph.mcp.artifacts.plan.plan_schema import ParallelPlanItem
from ralph.pydantic_compat import RalphBaseModel
from ralph.pydantic_validation_errors import (
    format_validation_error_messages,
    suggest_canonical_field,
)

if TYPE_CHECKING:
    from ralph.pipeline.work_unit import WorkUnit

# Closed intent_verb -> allowed ScopeCategory mapping. Every (verb, category)
# pair encountered in real engineering plans must be in the allowed set;
# otherwise the plan is rejected at normalize_plan_artifact_content time.
# The categories listed in each verb's allowed set are the closed ScopeCategory
# Literal values: bugfix, feature, refactor, test, docs, infra, migration,
# security, performance, cleanup, research, unknown, file_change, prompt,
# other.
_INTENT_VERB_ALLOWED_CATEGORIES: dict[str, frozenset[str]] = {
    "fix": frozenset({"bugfix", "file_change", "other", "unknown"}),
    "add": frozenset(
        {
            "feature",
            "infra",
            "test",
            "security",
            "performance",
            "docs",
            "migration",
            "refactor",
            "cleanup",
            "other",
            "file_change",
            "prompt",
            "unknown",
        }
    ),
    "refactor": frozenset({"refactor", "cleanup", "file_change", "other", "unknown"}),
    "migrate": frozenset({"migration", "refactor", "other", "file_change", "unknown"}),
    "document": frozenset({"docs", "other", "unknown"}),
    "investigate": frozenset({"research", "other", "unknown"}),
    "improve": frozenset(
        {
            "refactor",
            "feature",
            "performance",
            "test",
            "security",
            "docs",
            "infra",
            "cleanup",
            "other",
            "file_change",
            "prompt",
            "unknown",
        }
    ),
    "configure": frozenset({"infra", "security", "other", "unknown"}),
    "remove": frozenset({"cleanup", "refactor", "other", "file_change", "unknown"}),
}

# Shell-invocation denylist for VerificationStep.method. Each prefix is
# startswith() matched; the trailing space on each entry ensures legitimate
# commands like 'bash ./scripts/check.sh' (prefix 'bash ') are NOT blocked.
_SHELL_INVOCATION_PREFIXES: tuple[str, ...] = (
    "bash -c ",
    "sh -c ",
    "eval ",
)


class PlanArtifact(RalphBaseModel):
    """Top-level validated schema for a plan artifact."""

    model_config = ConfigDict(extra="forbid")

    summary: Summary
    skills_mcp: SkillsMcp
    steps: list[PlanStep] = Field(..., min_length=1)
    critical_files: CriticalFiles
    risks_mitigations: list[RiskMitigation] = Field(..., min_length=1)
    constraints: PlanConstraints | None = None
    noop: bool | None = Field(default=None, exclude=True)
    schema_version: int = Field(default=0, ge=0)
    design: DesignSection | None = None
    verification_strategy: list[VerificationStep] = Field(..., min_length=1)
    parallel_plan: list[ParallelPlanItem] = Field(default_factory=list)
    work_units: "list[WorkUnit]" = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_depends_on_acyclic(self) -> PlanArtifact:
        """Reject plans whose ``steps[*].depends_on`` graph contains a cycle.

        Mirrors the cycle-detector pattern in
        :func:`ralph.pipeline.work_units._validate_acyclic` (line 158 of
        ``ralph/pipeline/work_units.py``): DFS with two sets
        (``visiting`` = current DFS stack, ``visited`` = fully explored).
        A node that re-enters the ``visiting`` set is on a cycle; a node
        that re-enters the ``visited`` set is fine (it is a diamond / DAG
        with multiple parents).

        Runs FIRST inside the cross-reference validator (Pydantic v2
        calls ``@model_validator(mode='after')`` methods in source
        declaration order), so a cyclic graph is rejected before any
        other cross-section scan.

        Error message is stable: ``plan step depends_on cycle detected
        at step N`` where ``N`` is the step number that re-entered the
        DFS stack.
        """
        graph: dict[int, list[int]] = {step.number: list(step.depends_on) for step in self.steps}
        visiting: set[int] = set()
        visited: set[int] = set()

        def dfs(node: int) -> None:
            if node in visited:
                return
            if node in visiting:
                raise PlanArtifactValidationError(
                    f"plan step depends_on cycle detected at step {node!r}"
                )
            visiting.add(node)
            for dependency in graph.get(node, []):
                dfs(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            dfs(node)
        return self

    @model_validator(mode="after")
    def _validate_step_ac_cross_references(self) -> PlanArtifact:
        """Cross-validate the 2-way step<->acceptance-criterion link and 3 cross-section invariants.

        Each step's ``satisfies`` list must reference an AC id that exists on
        the plan, and each AC's ``satisfied_by_steps`` list must reference a
        real step number. Orphan links are rejected so the executor never
        consumes a stale or broken AC<->step link.

        After the cross-reference check, four additional invariants are
        enforced (each is a hard ``PlanArtifactValidationError``):

        1. ``summary.intent_verb`` -> ``scope_item.category`` compatibility.
           Every scope item whose category is NOT in the verb's allowed set
           is rejected with a message naming the offending text. The check
           skips when ``intent_verb`` is empty (the field is optional).
        2. ``parallel_plan`` and ``work_units`` are mutually exclusive. A
           plan that declares both raises a validation error.
        3. ``verification_strategy[*].method`` must not invoke a shell
           interpreter directly. Three strict ``startswith()`` prefixes are
           denied: ``bash -c ``, ``sh -c ``, ``eval `` (with trailing
           space so legitimate invocations like ``bash ./script.sh`` pass).
        4. ``design.acceptance_criteria`` entries must not reference a
           ``research`` or ``verify`` step in ``satisfied_by_steps``; only
           ``file_change`` and ``action`` steps can satisfy an AC.
        """
        criteria: list[AcceptanceCriterion] = _collect_criteria(self.design)
        ac_ids: set[str] = {c.id for c in criteria}
        step_numbers: set[int] = set()
        for s in self.steps:
            n: int = s.number
            step_numbers.add(n)

        _check_satisfies_links(self.steps, criteria, ac_ids)
        _check_satisfied_by_steps_links(criteria, step_numbers)

        # Cross-section invariant 1: intent_verb -> scope_item.category.
        _check_intent_verb_category_compatibility(
            self.summary.intent_verb, self.summary.scope_items
        )

        # Cross-section invariant 2: parallel_plan and work_units are
        # mutually exclusive.
        if self.parallel_plan and self.work_units:
            msg = "plan cannot declare both parallel_plan and work_units; pick one"
            raise PlanArtifactValidationError(msg)

        # Cross-section invariant 3: shell-invocation guard on verification.
        for step in self.verification_strategy:
            method = step.method
            for prefix in _SHELL_INVOCATION_PREFIXES:
                if method.startswith(prefix):
                    msg = (
                        "verification method must not invoke a shell interpreter "
                        "directly; use the executable path"
                    )
                    raise PlanArtifactValidationError(msg)

        # Cross-section invariant 4: research/verify steps cannot satisfy an AC.
        if criteria:
            step_type_by_number: dict[int, str] = {s.number: str(s.step_type) for s in self.steps}
            _check_research_verify_step_references(criteria, step_type_by_number)
        return self

    @model_validator(mode="before")
    @classmethod
    def _auto_fill_minimal_skills(cls, raw: object) -> object:
        """When planning_profile is 'minimal', auto-fill empty skills with default.

        Runs in mode='before' so the patch lands before SkillsMcp field-level
        validators (min_length=1 and normalize_skill_names empty-check) execute.
        """
        if not isinstance(raw, dict):
            return raw
        skills_mcp_raw: object = raw.get("skills_mcp")
        design_raw: object = raw.get("design")
        if not isinstance(skills_mcp_raw, dict) or not isinstance(design_raw, dict):
            return raw
        skills_value: object = skills_mcp_raw.get("skills")
        design_profile: object = design_raw.get("planning_profile")
        if isinstance(skills_value, list) and not skills_value and design_profile == "minimal":
            skills_mcp_raw["skills"] = cast("list[str]", ["writing-plans"])
        return raw


class _PlanArtifactRebuildState:
    rebuilt: bool = False


_PLAN_ARTIFACT_REBUILD_STATE = _PlanArtifactRebuildState()


def _ensure_plan_artifact_rebuilt() -> None:
    """Lazily resolve the TYPE_CHECKING forward reference to WorkUnit on PlanArtifact.

    Pydantic needs the class itself (not just the string) to resolve a forward
    reference used in a field annotation. We use import_module (deferred) to
    avoid the ralph.pipeline -> ralph.phases -> ralph.mcp.artifacts.plan
    circular import; the canonical WorkUnit lives in ralph.pipeline.work_unit.

    Idempotent: subsequent calls are no-ops once the model has been rebuilt.
    """
    if _PLAN_ARTIFACT_REBUILD_STATE.rebuilt:
        return
    work_unit_cls: type[object] = import_module("ralph.pipeline.work_unit").WorkUnit
    PlanArtifact.model_rebuild(
        _types_namespace=cast("dict[str, object]", {"WorkUnit": work_unit_cls}),
    )
    _PLAN_ARTIFACT_REBUILD_STATE.rebuilt = True


def is_noop_plan(artifact: Mapping[str, object]) -> bool:
    """Return True when ``artifact`` represents a planning no-op.

    An explicit ``noop: true`` marker is authoritative. As a defensive fallback,
    a plan with no ``steps`` and no ``work_units`` is also treated as a no-op
    so badly-shaped empty plans short-circuit cleanly instead of blowing up in
    schema validation downstream.
    """
    if artifact.get("noop") is True:
        return True
    steps = artifact.get("steps")
    work_units = artifact.get("work_units")
    steps_empty = steps is None or (isinstance(steps, list) and len(steps) == 0)
    work_units_empty = work_units is None or (isinstance(work_units, list) and len(work_units) == 0)
    return (
        steps_empty
        and work_units_empty
        and isinstance(steps, list)
        and isinstance(work_units, list)
    )


def normalize_plan_artifact_content(content: PlanArtifactDict) -> PlanArtifactDict:
    """Validate and normalize a raw plan artifact content dict.

    The size guard (``check_plan_size``) runs FIRST after the noop
    short-circuit so a runaway payload is rejected in < 100 ms before
    Pydantic ever touches it. The helper is PURE — it never raises —
    so the call site is a single ``if error is not None: raise`` with
    no try/except.
    """
    _ensure_plan_artifact_rebuilt()
    if is_noop_plan(content):
        return {"noop": True}
    size_error = check_plan_size(content)
    if size_error is not None:
        raise PlanArtifactValidationError(f"plan size violation: {size_error}")
    try:
        validated = PlanArtifact.model_validate(content)
        return validated.model_dump(
            mode="python",
            exclude_none=True,
            exclude_defaults=True,
        )
    except ValidationError as exc:
        raise PlanArtifactValidationError(_format_validation_error(exc)) from exc


def _format_validation_error(exc: ValidationError) -> str:
    """Format a pydantic ValidationError into an agent-friendly message.

    Uses the shared :mod:`ralph.pydantic_validation_errors` formatter to
    produce a field-level, value-aware message for every error in the
    exception. After the shared formatter runs, this helper appends a
    step_type remediation hint whenever the error set contains a
    ``steps[*].step_type`` literal_error; this preserves the long-standing
    ``"step_type"`` / ``"verify_command"`` substrings that existing
    ``pytest.raises(..., match=...)`` assertions expect.

    The remediation hint is appended *after* the shared formatter's
    output so the canonical field-level messages stay first and the
    step_type hint serves as a focused, plan-specific follow-up. The
    shared formatter runs once per ``ValidationError`` so the result is
    deterministic and never duplicates lines.

    An unknown top-level section (``design_constraints`` at the top
    level) is also surfaced as a follow-up hint that names the
    rejected key and suggests the closest canonical section.
    """
    shared_lines = format_validation_error_messages(exc)
    follow_ups: list[str] = []
    step_type_hint = _step_type_remediation_hint(exc)
    if step_type_hint is not None:
        follow_ups.append(step_type_hint)
    section_hint = _top_level_unknown_section_hint(exc)
    if section_hint is not None:
        follow_ups.append(section_hint)
    design_hint = _design_subkey_suggestion_hint(exc)
    if design_hint is not None:
        follow_ups.append(design_hint)
    if not follow_ups:
        return "\n".join(shared_lines) if shared_lines else str(exc)
    return "\n".join(shared_lines) + "\n" + "\n".join(follow_ups)


def _step_type_remediation_hint(exc: ValidationError) -> str | None:
    """Return a step_type remediation hint when the error set contains one.

    Returns ``None`` if no error in the exception targets
    ``steps[*].step_type`` so the shared formatter's output is used as-is
    in the common case. The hint is deterministic, names the four valid
    StepType members, and includes the canonical
    ``verify_command = 'pytest ...'`` remediation that the planning
    prompt recommends.
    """
    valid_step_types: tuple[str, ...] = (
        "file_change",
        "action",
        "research",
        "verify",
    )
    _min_step_type_loc_len: int = 3
    for err in _error_records(exc):
        loc_value: object = err.get("loc", ())
        loc: tuple[object, ...] = (
            cast("tuple[object, ...]", loc_value) if isinstance(loc_value, tuple) else ()
        )
        if (
            len(loc) >= _min_step_type_loc_len
            and loc[0] == "steps"
            and isinstance(loc[1], int)
            and loc[2] == "step_type"
        ):
            step_index: int = loc[1]
            input_value: object = err.get("input", None)
            return (
                f"step {step_index + 1} step_type={input_value!r} is not a valid value; "
                f"valid values are {list(valid_step_types)}. "
                f"For test-running steps use step_type='verify' with a "
                f"verify_command like 'pytest tests/test_x.py -q'."
            )
    return None


def _format_design_subkey_suggestion(exc: ValidationError) -> str | None:
    """Return a hint for an unknown design sub-section key, if any.

    When the plan dict contains a top-level field that does not match
    any known section, pydantic emits an ``extra_forbidden`` error at
    the top level. This helper detects those errors, names the rejected
    key, and suggests the closest canonical section name so the agent
    can self-correct without reading the schema.
    """
    for err in _error_records(exc):
        err_type = err.get("type")
        if err_type != "extra_forbidden":
            continue
        loc_obj = err.get("loc", ())
        loc: tuple[object, ...] = (
            cast("tuple[object, ...]", loc_obj)
            if isinstance(loc_obj, tuple)
            else ()
        )
        if not loc:
            continue
        if loc[0] != "":
            continue
        bad_key_obj = err.get("input")
        if not isinstance(bad_key_obj, str):
            continue
        suggestion = suggest_canonical_field(bad_key_obj, sorted(PLAN_SECTION_NAMES))
        if suggestion is None:
            return f"unknown section {bad_key_obj!r}; valid sections: {sorted(PLAN_SECTION_NAMES)}"
        return (
            f"unknown section {bad_key_obj!r}; did you mean {suggestion!r}? "
            f"valid sections: {sorted(PLAN_SECTION_NAMES)}"
        )
    return None


def _top_level_unknown_section_hint(exc: ValidationError) -> str | None:
    """Return a hint for an unknown top-level plan section, if any.

    This is a thin alias for :func:`_format_design_subkey_suggestion`
    kept for readability at the call site.
    """
    return _format_design_subkey_suggestion(exc)


_DESIGN_SECTION_KEYS: tuple[str, ...] = (
    "planning_profile",
    "constraints",
    "non_goals",
    "dependency_injection",
    "drift_detection",
    "testability",
    "refactor_strategy",
    "acceptance_criteria",
    "outcome",
    "notes",
)


_DESIGN_SUBSECTION_MIN_LOC_LEN: int = 2


def _design_subkey_suggestion_hint(exc: ValidationError) -> str | None:
    """Return a hint for an unknown ``design`` sub-section key, if any.

    Pydantic emits ``extra_forbidden`` with ``loc=('design', <bad>)``
    when the agent writes ``design.design_constraints`` (or similar)
    inside a top-level ``design`` block. When the agent validates the
    ``design`` section in isolation (via
    :func:`validate_plan_section`), the location collapses to
    ``loc=(<bad>,)`` because the ``DesignSection`` model is the
    validation target. This helper handles both shapes so the agent
    always sees the canonical sub-section list and the closest
    suggestion.
    """
    for err in _error_records(exc):
        err_type = err.get("type")
        if err_type != "extra_forbidden":
            continue
        loc_obj = err.get("loc", ())
        loc: tuple[object, ...] = (
            cast("tuple[object, ...]", loc_obj)
            if isinstance(loc_obj, tuple)
            else ()
        )
        if not loc:
            continue
        first = loc[0]
        bad_key_obj: object | None
        if first == "design" and len(loc) >= _DESIGN_SUBSECTION_MIN_LOC_LEN:
            bad_key_obj = loc[1]
        elif isinstance(first, str):
            bad_key_obj = first
        else:
            continue
        if not isinstance(bad_key_obj, str):
            continue
        suggestion = suggest_canonical_field(bad_key_obj, list(_DESIGN_SECTION_KEYS))
        if suggestion is None:
            return (
                f"unknown design sub-section {bad_key_obj!r}; "
                f"valid design sub-sections: {list(_DESIGN_SECTION_KEYS)}"
            )
        return (
            f"unknown design sub-section {bad_key_obj!r}; "
            f"did you mean design.{suggestion!r}? "
            f"valid design sub-sections: {list(_DESIGN_SECTION_KEYS)}"
        )
    return None


def _error_records(exc: ValidationError) -> list[dict[str, object]]:
    """Wrap ``ValidationError.errors()`` and re-cast to a clean dict type.

    Pydantic's ``ErrorDetails`` TypedDict has fields annotated with
    ``_Any`` so mypy with ``disallow_any_expr=True`` rejects direct
    iteration. This helper isolates the cast in one place so the
    consumer can iterate without spreading the cast across the loop.
    """
    raw = cast("list[object]", exc.errors())
    records: list[dict[str, object]] = [cast("dict[str, object]", r) for r in raw]
    return records


def _dump_model(model: RalphBaseModel) -> dict[str, object]:
    return model.model_dump(mode="python", exclude_none=True, exclude_defaults=True)


def _validate_list_item(
    section: str, item_model: type[RalphBaseModel], item: object
) -> dict[str, object]:
    if not isinstance(item, dict):
        raise PlanArtifactValidationError(f"section '{section}' items must be JSON objects")
    try:
        validated = item_model.model_validate(item)
    except ValidationError as exc:
        raise PlanArtifactValidationError(_format_validation_error(exc)) from exc
    return _dump_model(validated)


def validate_plan_section(
    section: str,
    payload: object,
    mode: SectionMode = "replace",
) -> object:
    """Validate a single plan section fragment against its submodel.

    Returns the normalized fragment (dict for object sections, list of dicts
    for list sections in replace mode, single dict for list sections in append
    mode). Raises PlanArtifactValidationError on any schema violation.
    """
    if section in PLAN_SECTION_OBJECT_MODELS:
        if mode != "replace":
            raise PlanArtifactValidationError(f"section '{section}' only supports mode='replace'")
        if not isinstance(payload, dict):
            raise PlanArtifactValidationError(f"section '{section}' must be a JSON object")
        model = PLAN_SECTION_OBJECT_MODELS[section]
        try:
            validated = model.model_validate(payload)
        except ValidationError as exc:
            raise PlanArtifactValidationError(_format_validation_error(exc)) from exc
        return _dump_model(validated)

    if section == "work_units":
        work_unit_model = cast(
            "type[RalphBaseModel]",
            import_module("ralph.pipeline.work_unit").WorkUnit,
        )
        if mode == "replace":
            if not isinstance(payload, list):
                raise PlanArtifactValidationError(
                    "section 'work_units' with mode='replace' must be a JSON array"
                )
            return [_validate_list_item(section, work_unit_model, item) for item in payload]
        if mode == "append":
            return _validate_list_item(section, work_unit_model, payload)
        raise PlanArtifactValidationError(f"unknown mode '{mode}' for section '{section}'")

    if section in PLAN_SECTION_LIST_ITEM_MODELS:
        item_model = PLAN_SECTION_LIST_ITEM_MODELS[section]
        if mode == "replace":
            if not isinstance(payload, list):
                raise PlanArtifactValidationError(
                    f"section '{section}' with mode='replace' must be a JSON array"
                )
            return [_validate_list_item(section, item_model, item) for item in payload]
        if mode == "append":
            return _validate_list_item(section, item_model, payload)
        raise PlanArtifactValidationError(f"unknown mode '{mode}' for section '{section}'")

    raise PlanArtifactValidationError(
        f"unknown plan section '{section}'. Valid sections: {sorted(PLAN_SECTION_NAMES)}"
    )


def merge_plan_section(
    sections: PlanArtifactDict,
    section: str,
    fragment: object,
    mode: SectionMode,
) -> PlanArtifactDict:
    """Return a new sections dict with the given fragment merged in."""
    new_sections: dict[str, object] = dict(sections)
    if section in PLAN_SECTION_OBJECT_MODELS or mode == "replace":
        new_sections[section] = fragment
        return new_sections

    existing = new_sections.get(section)
    base: list[object] = list(existing) if isinstance(existing, list) else []
    base.append(fragment)
    new_sections[section] = base
    return new_sections


def _collect_criteria(design: DesignSection | None) -> list[AcceptanceCriterion]:
    if design is None or design.acceptance_criteria is None:
        return []
    return list(design.acceptance_criteria.criteria)


def _check_satisfies_links(
    steps: list[PlanStep],
    criteria: list[AcceptanceCriterion],
    ac_ids: set[str],
) -> None:
    for step in steps:
        if not step.satisfies:
            continue
        if not criteria:
            msg = (
                f"step {step.number} declares satisfies entries but plan has no "
                "design.acceptance_criteria"
            )
            raise PlanArtifactValidationError(msg)
        for entry in step.satisfies:
            if entry not in ac_ids:
                msg = f"step {step.number} satisfies unknown acceptance criterion {entry!r}"
                raise PlanArtifactValidationError(msg)


def _check_satisfied_by_steps_links(
    criteria: list[AcceptanceCriterion],
    step_numbers: set[int],
) -> None:
    for criterion in criteria:
        # Read the runtime field directly to avoid a mypy static-analysis gap
        # where pydantic v2 updates __pydantic_fields__ without refreshing
        # the class __annotations__ that mypy reads.
        raw_refs: object = getattr(criterion, "satisfied_by_steps", [])
        refs: list[int] = (
            [raw for raw in raw_refs if isinstance(raw, int)] if isinstance(raw_refs, list) else []
        )
        for step_ref in refs:
            if step_ref not in step_numbers:
                msg = (
                    f"acceptance criterion {criterion.id!r} references unknown step "
                    f"number {step_ref}"
                )
                raise PlanArtifactValidationError(msg)


def _check_intent_verb_category_compatibility(
    intent_verb: str,
    scope_items: list[ScopeItem],
) -> None:
    """Reject scope items whose category is NOT in the verb's allowed set.

    Skips when ``intent_verb`` is empty (the field is optional; the user
    has not committed to a closed verb). The full 9-verb x 15-category
    mapping is documented at the top of this module.
    """
    if not intent_verb:
        return
    allowed = _INTENT_VERB_ALLOWED_CATEGORIES.get(intent_verb)
    if allowed is None:
        return
    for item in scope_items:
        category = item.category
        if category is None:
            continue
        if category not in allowed:
            msg = (
                f"scope item {item.text!r} has category {category!r} which is "
                f"incompatible with intent_verb={intent_verb!r}; change the verb to one "
                f"that admits {category!r} or split into multiple plans"
            )
            raise PlanArtifactValidationError(msg)


def _check_research_verify_step_references(
    criteria: list[AcceptanceCriterion],
    step_type_by_number: dict[int, str],
) -> None:
    """Reject ``satisfied_by_steps`` references to research/verify steps.

    Only ``file_change`` and ``action`` steps can satisfy an AC because
    they produce a concrete, observable change; research and verify
    steps are exploratory or pure-check and cannot be the authoritative
    completion signal for an AC.
    """
    for criterion in criteria:
        raw_refs: object = getattr(criterion, "satisfied_by_steps", [])
        refs: list[int] = (
            [raw for raw in raw_refs if isinstance(raw, int)] if isinstance(raw_refs, list) else []
        )
        for step_ref in refs:
            step_type = step_type_by_number.get(step_ref)
            if step_type in (StepType.RESEARCH, StepType.VERIFY):
                msg = (
                    "satisfied_by_steps cannot reference a research or verify step; "
                    f"step {step_ref} is {step_type!r} for criterion {criterion.id!r}"
                )
                raise PlanArtifactValidationError(msg)


def _decode_plan_payload(raw: str | Mapping[str, object]) -> PlanArtifactDict:
    """Canonical plan-payload decoder shared by the strict and lenient helpers.

    Accepts either a JSON string (decoded exactly once) or a mapping
    (shallow-copied to avoid leaking the caller's dict). Detects the
    ``{"type": "plan", "content": {...}}`` envelope and returns the
    inner ``content`` dict when it is a dict, otherwise raises
    ``PlanArtifactValidationError``.
    """
    if isinstance(raw, str):
        try:
            parsed: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlanArtifactValidationError(f"Content must be valid JSON: {exc}") from exc
    elif isinstance(raw, Mapping):
        parsed = dict(raw)
    else:
        raise PlanArtifactValidationError(
            f"plan payload must be a JSON string or mapping, got {type(raw).__name__}"
        )

    if not isinstance(parsed, dict):
        raise PlanArtifactValidationError("plan payload must decode to a JSON object")
    parsed_dict = cast("PlanArtifactDict", parsed)
    if parsed_dict.get("type") == "plan":
        nested = parsed_dict.get("content")
        if isinstance(nested, dict):
            return cast("PlanArtifactDict", nested)
        raise PlanArtifactValidationError("plan envelope has no valid 'content' object")
    return parsed_dict


def parse_plan_payload_strict(raw: str | Mapping[str, object]) -> PlanArtifactDict:
    """Strict plan-payload decoder that raises on any failure."""
    return _decode_plan_payload(raw)


def parse_plan_payload_lenient(
    raw: str | Mapping[str, object],
) -> PlanArtifactDict | None:
    """Lenient plan-payload decoder that returns None on the same failures."""
    try:
        return _decode_plan_payload(raw)
    except PlanArtifactValidationError:
        return None


def finalize_plan_draft(draft: PlanArtifactDict) -> PlanArtifactDict:
    """Validate a draft's sections as a whole PlanArtifact.

    Raises PlanArtifactValidationError if any cross-section invariant fails
    (e.g. a required section is still missing).
    """
    sections = draft.get("sections")
    if not isinstance(sections, dict):
        raise PlanArtifactValidationError("plan draft is missing a 'sections' object")
    return normalize_plan_artifact_content(cast("PlanArtifactDict", sections))


def generate_plan_schema() -> dict[str, object]:
    """Return the JSON Schema for ``PlanArtifact`` as a Python dict.

    The on-disk file at ``ralph/mcp/artifacts/plan/schema.json`` is generated
    from this helper (via ``PlanArtifact.model_json_schema()``) and locked
    by the regression test ``test_schema_json_file_matches_generate_plan_schema``.
    External tools (mypy, pyright, vscode) can consume the on-disk file to
    type-check a plan without round-tripping Pydantic.
    """
    _ensure_plan_artifact_rebuilt()
    return PlanArtifact.model_json_schema()


__all__ = [
    "PlanArtifact",
    "PlanArtifactValidationError",
    "SectionMode",
    "finalize_plan_draft",
    "generate_plan_schema",
    "is_noop_plan",
    "merge_plan_section",
    "normalize_plan_artifact_content",
    "parse_plan_payload_lenient",
    "parse_plan_payload_strict",
    "validate_plan_section",
]
