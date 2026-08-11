"""Markdown mapping and validation rules for ``design_verdict`` artifacts.

Consumed structure (stays strict): frontmatter ``type`` (closed
vocabulary ``design_verdict``), the required-section skeleton
(``## Capture Provenance`` body fields, ``## Design Intent`` one
item, ``## Verdict`` one item shaped ``status | summary``, and
``## Findings`` items shaped
``capture_id | x,y,w,h | dimension | severity | narrative``), and
the cross-section invariants that the consumer relies on:

- Every ``capture_id`` cited in a finding must appear in the
  ``cell_ids`` list of ``## Capture Provenance``. A finding that
  references a cell the artifact did not actually capture is a
  hard error because downstream consumers resolve the cell to a
  rendered image and an unknown cell would render an empty
  rectangle — a verdict built on phantom evidence.
- ``## Verdict`` status must match the findings it reports. A
  ``pass`` status requires the findings contain no ``blocker`` or
  ``major`` severities (otherwise the verdict would be praising a
  regression). A ``fail`` status requires at least one ``blocker``
  or ``major`` severity (otherwise the verdict is rejecting a
  passing review). ``blocked`` is reserved for verdicts the
  reviewer could not complete and is independent of finding
  severities.
- ``## Design Intent`` is the verbatim text the agent was asked to
  review; smuggle phrases (``source``, ``diff``, ``DOM``,
  ``stylesheet``) that try to pivot the review into a code-reading
  task are rejected so a verdict cannot be smuggled in by escaping
  the visual review.

Section bodies tolerate multi-line prose and unknown continuation
lines under items; the consumed structure above is what this spec
checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ralph.mcp.artifacts.design_verdict import (
    DESIGN_VERDICT_ARTIFACT_TYPE,
    normalize_design_verdict_content,
)
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._frontmatter_vocabulary import FrontmatterVocabulary
from ralph.mcp.artifacts.markdown._section_rule import SectionRule
from ralph.mcp.artifacts.markdown._spec import Content, MdArtifactSpec
from ralph.mcp.artifacts.markdown.registry import register_spec

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection

_STATUSES: tuple[Literal["pass", "fail", "blocked"], ...] = ("pass", "fail", "blocked")
_JUDGEMENT_TIERS: tuple[Literal["deterministic", "on-demand"], ...] = (
    "deterministic",
    "on-demand",
)
_FINDING_PARTS = 5
_VERDICT_PARTS = 2
_REGION_COORDINATES = 4
_SMUGGLE_PHRASES: tuple[str, ...] = ("source", "diff", "DOM", "stylesheet")
_BLOCKING_SEVERITIES: frozenset[str] = frozenset({"blocker", "major"})


def _required_section(document: ParsedDocument, name: str) -> ParsedSection:
    section = document.section(name)
    if section is None:
        raise ValueError(f"missing required section {name!r}")
    return section


def _one_item(document: ParsedDocument, name: str) -> str:
    section = _required_section(document, name)
    if len(section.items) != 1:
        raise ValueError(f"{name} must contain exactly one item")
    return section.items[0].text


def _provenance_field(
    section: ParsedSection,
    field: str,
    *,
    allow_missing: bool = False,
) -> str:
    """Return the value of a single ``Field: value`` body line in a section.

    Field lines are body lines (not list items) — they live in
    ``section.lines`` after the parser drops item-shaped content.
    A missing field raises unless ``allow_missing`` is set so the
    provenance field check can short-circuit when the section is
    not present.
    """
    prefix = f"{field}:"
    for line in section.lines:
        if line.text == prefix:
            return ""
        if line.text.startswith(prefix + " "):
            return line.text[len(prefix) + 1 :]
    if allow_missing:
        return ""
    raise ValueError(f"## Capture Provenance must declare '{field}:' field")


def _required_provenance_field(section: ParsedSection, field: str) -> str:
    """Return the value of a required provenance field, raising if missing."""
    value = _provenance_field(section, field)
    if not value:
        raise ValueError(f"## Capture Provenance '{field}:' value is required")
    return value


def _parse_region(region: str) -> tuple[int, int, int, int] | None:
    """Parse an ``x,y,w,h`` region. Returns ``None`` for any malformed shape."""
    parts = region.split(",")
    if len(parts) != _REGION_COORDINATES:
        return None
    parsed: list[int] = []
    for part in parts:
        try:
            value = int(part)
        except ValueError:
            return None
        if value < 0:
            return None
        parsed.append(value)
    return parsed[0], parsed[1], parsed[2], parsed[3]


def _cell_ids_from_provenance(
    provenance: ParsedSection | None,
) -> set[str]:
    """Read the comma-separated ``cell_ids`` field into a set for fast lookup."""
    if provenance is None:
        return set()
    raw = _provenance_field(provenance, "cell_ids", allow_missing=True)
    return {entry.strip() for entry in raw.split(",") if entry.strip()}


def _parse_findings(section: ParsedSection) -> list[dict[str, object]]:
    """Extract well-formed finding dicts from the findings section.

    Malformed entries (wrong number of pipe-separated parts) are
    skipped here; the cross-section ``_validate_document`` hook
    reports a diagnostic for each so the agent can see exactly
    which entry needs repair.
    """
    findings: list[dict[str, object]] = []
    for item in section.items:
        parts = item.text.split(" | ", _FINDING_PARTS - 1)
        if len(parts) != _FINDING_PARTS:
            continue
        capture_id, region, dimension, severity, narrative = parts
        findings.append(
            {
                "capture_id": capture_id.strip(),
                "region": region.strip(),
                "dimension": dimension.strip(),
                "severity": severity.strip(),
                "narrative": narrative.strip(),
            }
        )
    return findings


def _to_content(document: ParsedDocument) -> Content:
    """Map the parsed markdown document to the canonical content dict."""
    provenance = _required_section(document, "Capture Provenance")
    run_id = _required_provenance_field(provenance, "run_id")
    judgement_tier = document.frontmatter.get("judgement_tier")
    target = _required_provenance_field(provenance, "target")
    before_id = _required_provenance_field(provenance, "before_id")
    after_id = _required_provenance_field(provenance, "after_id")
    cell_ids_raw = _required_provenance_field(provenance, "cell_ids")
    cell_ids = [entry.strip() for entry in cell_ids_raw.split(",") if entry.strip()]
    if not cell_ids:
        raise ValueError("## Capture Provenance 'cell_ids:' must list at least one capture id")
    verdict_id = _provenance_field(provenance, "verdict_id", allow_missing=True)
    before_handles = tuple(
        handle.strip()
        for handle in _provenance_field(provenance, "before_handles", allow_missing=True).split(",")
        if handle.strip()
    )
    after_handles = tuple(
        handle.strip()
        for handle in _provenance_field(provenance, "after_handles", allow_missing=True).split(",")
        if handle.strip()
    )
    intent = _one_item(document, "Design Intent")
    verdict_text = _one_item(document, "Verdict")
    verdict_parts = verdict_text.split(" | ", 1)
    if len(verdict_parts) != _VERDICT_PARTS:
        raise ValueError("## Verdict must use 'status | summary' shape")
    status, summary = verdict_parts
    findings = _parse_findings(_required_section(document, "Findings"))
    return {
        "run_id": run_id,
        "judgement_tier": judgement_tier,
        "verdict_id": verdict_id or None,
        "target": target,
        "before_id": before_id,
        "after_id": after_id,
        "cell_ids": cell_ids,
        "before_handles": before_handles,
        "after_handles": after_handles,
        "intent": intent,
        "status": status.strip(),
        "summary": summary.strip(),
        "findings": findings,
    }


def _validate_finding(
    item_text: str,
    item_line: int,
    cell_ids: set[str],
) -> tuple[list[Diagnostic], str]:
    """Validate one finding line. Returns (diagnostics, severity or '')."""
    diagnostics: list[Diagnostic] = []
    parts = item_text.split(" | ", _FINDING_PARTS - 1)
    if len(parts) != _FINDING_PARTS:
        diagnostics.append(
            Diagnostic(
                item_line,
                "Findings",
                "DV003",
                "finding must use 'capture_id | x,y,w,h | dimension | severity | narrative'",
            )
        )
        return diagnostics, ""
    capture_id = parts[0].strip()
    region = parts[1].strip()
    severity = parts[3].strip()
    if not capture_id:
        diagnostics.append(
            Diagnostic(item_line, "Findings", "DV003", "finding capture_id is empty")
        )
    elif cell_ids and capture_id not in cell_ids:
        diagnostics.append(
            Diagnostic(
                item_line,
                "Findings",
                "DV003",
                f"finding references unknown capture_id {capture_id!r}; "
                "every capture_id must appear in '## Capture Provenance' cell_ids "
                "so downstream consumers can resolve the rendered cell",
            )
        )
    if _parse_region(region) is None:
        diagnostics.append(
            Diagnostic(
                item_line,
                "Findings",
                "DV004",
                "finding region must use non-negative 'x,y,w,h' integer shape",
            )
        )
    if not severity:
        diagnostics.append(
            Diagnostic(
                item_line,
                "Findings",
                "DV005",
                "finding severity is empty; use one of blocker/major/minor/info",
            )
        )
    return diagnostics, severity


def _validate_verdict(
    verdict_text: str,
    verdict_line: int,
    severities: list[str],
) -> list[Diagnostic]:
    """Validate the verdict line against the collected finding severities."""
    verdict_parts = verdict_text.split(" | ", 1)
    status = verdict_parts[0].strip() if verdict_parts else ""
    has_blocking = any(severity in _BLOCKING_SEVERITIES for severity in severities)
    if status == "pass" and has_blocking:
        return [
            Diagnostic(
                verdict_line,
                "Verdict",
                "DV006",
                "verdict 'pass' but findings include blocker/major severity; "
                "a passing verdict cannot coexist with blocking findings",
            )
        ]
    if status == "fail" and severities and not has_blocking:
        return [
            Diagnostic(
                verdict_line,
                "Verdict",
                "DV007",
                "verdict 'fail' but findings contain no blocker/major severity; "
                "a failing verdict requires at least one blocking finding",
            )
        ]
    return []


def _validate_intent(intent_text: str, intent_line: int) -> list[Diagnostic]:
    """Reject intent text that smuggles a source-reading phrase."""
    return [
        Diagnostic(
            intent_line,
            "Design Intent",
            "DV008",
            f"intent smuggles forbidden phrase {phrase!r}; "
            "design_verdict review is a visual check and the intent "
            "must not pivot into a source/diff/DOM/stylesheet reading task",
        )
        for phrase in _SMUGGLE_PHRASES
        if phrase in intent_text
    ]


def _validate_document(document: ParsedDocument) -> list[Diagnostic]:
    """Enforce cross-section invariants the markdown mapper cannot express."""
    diagnostics: list[Diagnostic] = []
    cell_ids = _cell_ids_from_provenance(document.section("Capture Provenance"))
    severities: list[str] = []
    findings_section = document.section("Findings")
    if findings_section is not None:
        for item in findings_section.items:
            finding_diagnostics, severity = _validate_finding(
                item.text, item.line, cell_ids
            )
            diagnostics.extend(finding_diagnostics)
            if severity:
                severities.append(severity)
    verdict_section = document.section("Verdict")
    if verdict_section is not None and len(verdict_section.items) == 1:
        diagnostics.extend(
            _validate_verdict(verdict_section.items[0].text, verdict_section.items[0].line, severities)
        )
    intent_section = document.section("Design Intent")
    if intent_section is not None and intent_section.items:
        diagnostics.extend(
            _validate_intent(intent_section.items[0].text, intent_section.items[0].line)
        )
    return diagnostics


def _normalize(content: dict[str, object]) -> dict[str, object]:
    return normalize_design_verdict_content(content)


DESIGN_VERDICT_SPEC = MdArtifactSpec(
    artifact_type=DESIGN_VERDICT_ARTIFACT_TYPE,
    required_frontmatter=frozenset({"type"}),
    closed_frontmatter={
        "type": FrontmatterVocabulary((DESIGN_VERDICT_ARTIFACT_TYPE,), "DV001"),
        "status": FrontmatterVocabulary(_STATUSES, "DV002"),
        "judgement_tier": FrontmatterVocabulary(_JUDGEMENT_TIERS, "DV009"),
    },
    sections={
        "Capture Provenance": SectionRule(required=True, allow_body=True),
        "Design Intent": SectionRule(
            require_items=True, max_items=1, allow_body=True
        ),
        "Verdict": SectionRule(require_items=True, max_items=1, allow_body=True),
        "Findings": SectionRule(required=True, allow_body=True),
    },
    to_content=_to_content,
    normalize_content=_normalize,
    validate_document=_validate_document,
    allow_unknown_frontmatter=True,
    allow_unknown_sections=True,
)

register_spec(DESIGN_VERDICT_SPEC)

__all__ = ["DESIGN_VERDICT_SPEC"]
