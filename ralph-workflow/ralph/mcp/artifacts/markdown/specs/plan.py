"""Markdown mapping and best-effort extraction for ``plan`` artifacts.

This module is the plan-shape spec consumed by the shared markdown parser.
It does NOT validate plan structure: the only rejection is
``PLAN001`` (recognizably not a plan), the size cap from
``check_plan_size``, and the ``noop: true`` short-circuit. Everything
else — step IDs, dependencies, work units, paths, files — is parsed
on a best-effort basis. Malformed or missing structure produces a
partial extraction, never an error.

The single contract is documented in
``ralph/mcp/artifacts/format_docs/plan.md`` and the docstring at the
top of ``analyze_plan_document`` below. The shared markdown parser
applies this spec to every ``type: plan`` submission; the consumer
side is ``_development_result_session_gate.py`` (proofs), the
``phases/execution.py`` development phase, and the work-unit scheduler.

A short example of a contract the spec ENFORCES:

- ``noop: true`` frontmatter and a plan with empty content is a
  no-op. The ``Content`` returned is ``{"noop": True}`` and the
  spec short-circuits before any other check.
- Anything else that fails ``PLAN001`` (binary, refusal, empty, too
  short) returns ``{}`` with the ``PLAN001`` diagnostic attached.

A short example of a contract the spec TOLERATES (no error):

- Prose plan with no headings, no step IDs, no work units, no
  ownership. The extraction returns an empty content dict and no
  diagnostics; downstream consumers fall back to a single plan-level
  proof entry (see ``phases/execution.py``).
- Plan with duplicate step IDs, dangling dependencies, cyclic
  dependencies, empty designs, both ``## Work Units`` and
  ``## Parallel Plan``, or work units whose owned files overlap.
- Plan with malformed frontmatter, missing ``type: plan``, or
  unknown frontmatter keys. The size guard still applies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown._section_rule import SectionRule
from ralph.mcp.artifacts.markdown._spec import Content, MdArtifactSpec, parse_and_validate
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.markdown.specs._plan_not_a_plan import (
    detect_not_a_plan,
)
from ralph.mcp.artifacts.plan._section_registry import PLAN_ARTIFACT_TYPE
from ralph.mcp.artifacts.plan._size_limits import check_plan_size
from ralph.mcp.artifacts.plan.plan_artifact_validation_error import (
    PlanArtifactValidationError,
)

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
    from ralph.mcp.artifacts.markdown._document import ParsedDocument


# ---------------------------------------------------------------------------
# Best-effort extraction patterns.
# ---------------------------------------------------------------------------

# Loose step heading: anything that looks like a step identifier is captured.
_STEP_HEADING_PATTERN: re.Pattern[str] = re.compile(r"^\s*#{2,6}\s+\[?(?P<id>[A-Za-z][A-Za-z0-9._-]*)\]?")
_STEP_NUMBER_PATTERN: re.Pattern[str] = re.compile(r"^S-(?P<number>[1-9][0-9]*)$")

# Recognise ``Depends on: S-1, S-2`` or ``Depends on: S-1`` inline.
_DEPENDS_ON_PATTERN: re.Pattern[str] = re.compile(r"^\s*Depends\s+on:\s*(?P<refs>.+?)\s*$", re.IGNORECASE)
_INLINE_REF_PATTERN: re.Pattern[str] = re.compile(r"S-\d+", re.IGNORECASE)

# Recognise ``Files:`` blocks used to recover a unit's owned files when
# the unit declares neither ``Directories:`` nor ``Paths:``.
_FILES_FIELD_PATTERN: re.Pattern[str] = re.compile(r"^\s*Files:\s*$", re.IGNORECASE)
_PATH_BULLET_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*-\s*(?P<action>modify|create|delete|read|run|test|verify)?\s*`?(?P<path>\S+?)`?\s*$",
    re.IGNORECASE,
)
_DIRECTORIES_FIELD_PATTERN: re.Pattern[str] = re.compile(r"^\s*Directories:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
_PATHS_FIELD_PATTERN: re.Pattern[str] = re.compile(r"^\s*Paths:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
_DEPENDS_FIELD_PATTERN: re.Pattern[str] = re.compile(r"^\s*Depends\s+on:\s*(?P<value>.+?)\s*$", re.IGNORECASE)

# Section headings recognised for parallel decomposition. These names are
# case-folded and whitespace-collapsed in the parser already.
_FAN_OUT_SECTIONS: tuple[str, ...] = ("Work Units", "Parallel Plan")


# ---------------------------------------------------------------------------
# Public entry point: analyze_plan_document.
# ---------------------------------------------------------------------------


def analyze_plan_document(text: str) -> tuple[Content, list[Diagnostic], list[object]]:
    """Parse a plan markdown document with sanity-only acceptance.

    Returns a triple of (content, diagnostics, override_ledger). The
    override ledger is a backward-compatibility placeholder (always empty)
    so callers that used the prior three-tuple signature keep working.

    The function enforces only the sanity checks agreed in the request:

    - PLAN001: the document is recognizably not a plan (binary,
      refusal, empty, under the readability/word floor, or
      misrouted from another artifact).
    - The 4 MB raw-UTF-8 size cap (via the size guard, when the
      raw text exceeds the cap).
    - The ``noop: true`` short-circuit.

    Structural problems (duplicate step IDs, dangling or cyclic
    dependencies, malformed work units, unknown keys, missing
    sections, etc.) are absorbed silently into the returned
    ``content`` (a best-effort extraction). The returned diagnostic
    list contains only the sanity failures; structure is never a
    diagnostic.
    """
    content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    not_a_plan_diagnostics = [d for d in diagnostics if d.rule_id == "PLAN001"]
    if not_a_plan_diagnostics:
        return {}, diagnostics, []
    return content, diagnostics, []


def _is_minimal_noop(document: ParsedDocument) -> bool:
    """Return True for a canonical ``noop: true`` with no body content.

    The historical contract requires the only frontmatter keys be
    ``type: plan`` and ``noop: true`` and the document body have no
    sections. Anything else is treated as an active plan (the body
    matters even if ``noop: true`` is set).
    """
    if document.frontmatter.get("noop") != "true":
        return False
    if set(document.frontmatter.keys()) - {"type", "noop"}:
        return False
    return not document.sections


def _best_effort_extract(document: ParsedDocument, raw_text: str) -> Content:
    """Extract whatever structure we can find without raising.

    Returns a content dict with one or more of: ``steps`` (a list
    of best-effort step dicts), ``work_units`` (a list of best-effort
    work-unit dicts), ``parallel_plan`` (a list of best-effort
    parallel-plan items), and ``noop`` (True when applicable).
    Malformed, missing, or duplicated structure is dropped silently.
    """
    content: Content = {}
    steps = _extract_steps(document)
    if steps:
        content["steps"] = steps
    work_units = _extract_work_units(document, raw_text, section_name="Work Units")
    parallel_units = _extract_work_units(document, raw_text, section_name="Parallel Plan")
    if work_units:
        content["work_units"] = work_units
    if parallel_units:
        content["parallel_plan"] = parallel_units
    if (
        document.frontmatter.get("noop", "").lower() == "true"
    ):
        content["noop"] = True
    return content


# ---------------------------------------------------------------------------
# Step extraction.
# ---------------------------------------------------------------------------


def _extract_steps(document: ParsedDocument) -> list[Content]:
    """Collect ``### [S-n]`` headings and any visible body text.

    Each step is ``{"id": "S-n", "number": n, "title": ..., "body": ...,
    "depends_on": [...]}``. Duplicates are deduplicated by ``id`` with
    first-wins. Non-``S-n`` headings are ignored. ``Depends on:`` is
    parsed out of the body when present.
    """
    seen: dict[str, Content] = {}
    for section in document.sections:
        for block in section.blocks:
            identifier = block.identifier or ""
            number_match = _STEP_NUMBER_PATTERN.match(identifier)
            step_id = f"S-{number_match.group('number')}" if number_match else identifier
            if not step_id or step_id in seen:
                continue
            body_text = "\n".join(line.text for line in block.lines)
            depends_on = _parse_depends_on(body_text)
            step: Content = {
                "id": step_id,
                "number": int(number_match.group("number")) if number_match else None,
                "title": block.title or step_id,
                "body": body_text,
                "depends_on": depends_on,
            }
            seen[step_id] = step
    return list(seen.values())


def _parse_depends_on(body: str) -> list[str]:
    """Pull ``S-n`` references out of ``Depends on:`` lines in a step body."""
    if not body:
        return []
    refs: list[str] = []
    for line in body.splitlines():
        if not line.casefold().startswith("depends on:"):
            continue
        _label, _separator, references = line.partition(":")
        for candidate in references.replace(",", " ").split():
            normalised = candidate.upper().replace("STEP-", "S-")
            if re.fullmatch(r"S-\d+", normalised) and normalised not in refs:
                refs.append(normalised)
    return refs


# ---------------------------------------------------------------------------
# Work-unit extraction.
# ---------------------------------------------------------------------------


def _extract_work_units(
    document: ParsedDocument, raw_text: str, *, section_name: str
) -> list[Content]:
    """Parse a fan-out section (Work Units or Parallel Plan) tolerantly.

    Each unit captures whatever ownership signals are present
    (Directories, Paths, Files) and a Depends on list. Units are
    deduplicated by ``unit_id`` with first-wins. Malformed unit
    headings (e.g. missing brackets) are absorbed by treating the
    heading text as the unit id.
    """
    if not any(section.name == section_name for section in document.sections):
        return []
    units: list[Content] = []
    seen_ids: set[str] = set()
    for section in document.sections:
        if section.name != section_name:
            continue
        for item in section.items:
            unit_id = item.identifier or item.text or ""
            if not unit_id or unit_id in seen_ids:
                continue
            seen_ids.add(unit_id)
            unit: Content = {
                "unit_id": unit_id,
                "description": item.text or "",
                "directories": [],
                "paths": [],
                "files": [],
                "dependencies": [],
            }
            # The body lines for an item follow the item heading; the
            # parser exposes them through ``document`` (sections
            # carry ``lines`` with field-style entries). Re-parse
            # the unit body by walking lines that belong to the
            # unit heading.
            # Parsed continuation fields retain unit-local ownership without
            # relying on reconstructed source offsets.
            unit_lines = [field.text for field in item.fields]
            _populate_unit_fields(unit, unit_lines)
            units.append(unit)
    # Best-effort: when a unit has no ownership signals, fall back
    # to a Files list from the steps inside the unit (this is
    # deliberately a no-op here — the executor path reconstructs
    # ownership from the unit's nested step content).
    return units


def _append_unit_values(unit: Content, key: str, values: list[str]) -> None:
    """Append distinct usable values to one tolerant unit field."""
    destination = unit.get(key)
    if not isinstance(destination, list):
        return
    for value in values:
        if value and value not in destination:
            destination.append(value)


def _populate_unit_fields(unit: Content, lines: list[str]) -> None:
    """Fill a unit with any recognised ownership and dependency fields."""
    in_files_block = False
    fields = (
        (_DIRECTORIES_FIELD_PATTERN, "directories"),
        (_PATHS_FIELD_PATTERN, "paths"),
        (_DEPENDS_FIELD_PATTERN, "dependencies"),
    )
    for line in lines:
        if not line.strip():
            in_files_block = False
            continue
        matched_field = next(
            ((match, key) for pattern, key in fields if (match := pattern.match(line)) is not None),
            None,
        )
        if matched_field is not None:
            match, key = matched_field
            _append_unit_values(unit, key, _split_csv(match.group("value")))
            in_files_block = False
        elif _FILES_FIELD_PATTERN.match(line):
            in_files_block = True
        elif in_files_block and (match := _PATH_BULLET_PATTERN.match(line)) is not None:
            _append_unit_values(unit, "files", [match.group("path")])


def _split_csv(value: str) -> list[str]:
    """Split a comma- or whitespace-separated value list."""
    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return [item.strip() for item in value.split() if item.strip()]


# ---------------------------------------------------------------------------
# Normalization hook used by the shared parser.
# ---------------------------------------------------------------------------


def _normalize_plan_content(content: Content) -> Content:
    """Sanity-only normalization for plan content.

    Only the size guard can raise here. Everything else is
    tolerated — structural fields are passed through as-is so
    downstream consumers see the same shape the markdown mapper
    produced. Missing structure yields an empty ``Content`` so a
    truly malformed plan still canonicalizes.
    """
    size_error = check_plan_size(content)
    if size_error is not None:
        raise PlanArtifactValidationError(f"plan size violation: {size_error}")
    return content


# ---------------------------------------------------------------------------
# Validation hooks used by the shared parser.
# ---------------------------------------------------------------------------


def _to_content(document: ParsedDocument) -> Content:
    """Map a parsed plan document into the best-effort content dict.

    The shared parser calls this hook when no error diagnostics
    short-circuit earlier. We rebuild the same ``Content`` shape
    ``analyze_plan_document`` would return, but without going
    through the parser again.
    """
    return _best_effort_extract(document, _reconstructed_text(document))


def _reconstructed_text(document: ParsedDocument) -> str:
    """Render a minimal text representation of the document."""
    parts: list[str] = []
    if document.frontmatter:
        parts.append("---")
        for key, value in document.frontmatter.items():
            parts.append(f"{key}: {value}")
        parts.append("---")
    return "\n".join(parts)


def _document_warnings(document: ParsedDocument) -> list[Diagnostic]:
    """Plan-scoped ``validate_document`` hook.

    Returns no diagnostics. The spec is sanity-only; structural
    warnings (e.g. duplicate step IDs) are deliberately not
    emitted so the development phase and analyzer see the raw
    plan without false signals.
    """
    del document
    return []


# ---------------------------------------------------------------------------
# Spec registration.
# ---------------------------------------------------------------------------


_FAN_OUT_SECTION_RULE = SectionRule(
    required=False, repeatable=True, allow_body=True, allow_blocks=True, allow_items=True
)


def _has_section_structure(document: ParsedDocument) -> bool:
    """A plan is structured when it has at least one ``## Heading`` section.

    A document with no ``##`` sections is a prose plan. The shared parser
    uses this predicate to skip body-grammar rules (MD001-MD004) so
    free-form prose plans are accepted without parser-level errors.
    """
    return any(section.name for section in document.sections)


PLAN_SPEC = MdArtifactSpec(
    artifact_type=PLAN_ARTIFACT_TYPE,
    required_frontmatter=frozenset({"type"}),
    optional_frontmatter=frozenset({"noop"}),
    allow_unknown_frontmatter=True,
    allow_nested_headings=True,
    sections={
        "Parallel Plan": _FAN_OUT_SECTION_RULE,
        "Work Units": _FAN_OUT_SECTION_RULE,
    },
    allow_unknown_sections=True,
    to_content=_to_content,
    normalize_content=_normalize_plan_content,
    validate_document=_document_warnings,
    validate_text=detect_not_a_plan,
    severity_policy=None,
    minimal_variant=None,
    structured_body=_has_section_structure,
)


register_spec(PLAN_SPEC)

__all__ = ["PLAN_SPEC", "analyze_plan_document"]


# Backward-compatibility placeholder for the validation-override ledger
# removed in this revision. The shared parser no longer emits overrides
# (validation overrides are no longer a thing), but external code may
# still import the name; keep a minimal dataclass that satisfies
# ``isinstance`` checks in the tool payload layer.
@dataclass(frozen=True)
class _OverrideMatch:
    """Legacy override ledger entry — preserved as a stable, opaque type."""

    rule_id: str = ""
    section: str | None = None
    reason: str = ""
    diagnostic: Diagnostic | None = None
