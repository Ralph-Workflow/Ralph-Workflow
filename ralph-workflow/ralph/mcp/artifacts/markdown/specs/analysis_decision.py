"""Markdown specs for planning, development, review, and policy decisions.

Development analysis decisions are free-form: only the ``status`` enum is
validated, because routing reads it. The rest of the body is written for the
next agent to read and act on, not for a validator to check.

Planning, review, and policy-remediation decisions keep their structured
validation verbatim (out of scope here).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown import MdArtifactSpec, SectionRule
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._frontmatter_vocabulary import FrontmatterVocabulary
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.typed_artifacts import normalize_analysis_decision_content

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem

_ANALYSIS_TYPES = (
    "planning_analysis_decision",
    "development_analysis_decision",
    "review_analysis_decision",
    "policy_remediation_analysis_decision",
)
_VERIFICATION_TYPES = frozenset(_ANALYSIS_TYPES) - {"review_analysis_decision"}
_STATUSES = ("completed", "request_changes", "failed")
_FINDING_TARGET_PATTERN = re.compile(r"(?:Step:\s*)?\[(S-[1-9][0-9]*)\]|Plan-level:", re.IGNORECASE)
_STEP_REFERENCE_PATTERN = re.compile(r"Step:\s*\[(S-[1-9][0-9]*)\]")
_REQUIRED_VERDICT_FIELDS = (
    "Criterion:",
    "Expected observation:",
    "Verdict:",
    "Evidence:",
    "Location:",
)
#: planning_analysis_decision verdicts are the only ones that must
#: include ``Cost:``; the planning contract treats the cost of the
#: missed split as part of the verdict, while development and policy
#: decisions document what remains via ``Remaining work:`` and never
#: duplicate the cost on every verdict.
_PLANNING_VERDICT_FIELDS = (*_REQUIRED_VERDICT_FIELDS, "Cost:")
_VERDICT_PATTERN = re.compile(r"Verdict:\s*(met|not met|not evaluable)(?:\.|$)", re.IGNORECASE)
_REQUIRED_FINDING_FIELDS = tuple(
    field for field in _REQUIRED_VERDICT_FIELDS if field != "Criterion:"
)


def _finding_fields_complete(text: str) -> bool:
    """Return True when a What Came Up Short item identifies its subject and
    carries every required finding field.

    The subject slot accepts ``Criterion:`` or any ``Plan reference: [<id>]``
    the plan actually uses; free-form plans are not required to use ``S-n``
    identifiers, so the plan-reference check is deliberately non-specific.
    """
    has_subject = "Criterion:" in text or _PLAN_REFERENCE_PATTERN.search(text) is not None
    return has_subject and all(field in text for field in _REQUIRED_FINDING_FIELDS)


def _has_finding_target(text: str) -> bool:
    """A finding targets the plan via a step, plan-level scope, or any stable
    plan reference identifier (free-form plans need not use S-n step IDs)."""
    return (
        _FINDING_TARGET_PATTERN.search(text) is not None
        or _PLAN_REFERENCE_PATTERN.search(text) is not None
    )


def _extract_verdict(text: str) -> str | None:
    """Extract the lowercased verdict from a finding/verdict line, or ``None``."""
    match = _VERDICT_PATTERN.search(text)
    if match is None:
        return None
    value = match.group(1)
    return value.casefold() if isinstance(value, str) else None


_EVIDENCE_PATTERN = re.compile(r"Evidence:\s*(.*?)(?=\s*Location:|$)", re.IGNORECASE)
_LOCATION_PATTERN = re.compile(r"Location:\s*(.*?)\s*$", re.IGNORECASE)
_REMAINING_WORK_PATTERN = re.compile(r"Remaining work:\s*(.+)", re.IGNORECASE)
_PLAN_REFERENCE_PATTERN = re.compile(r"Plan reference:\s*\[[^\]]+\]", re.IGNORECASE)
_PLACEHOLDER_LOCATIONS = frozenset({"unknown", "n/a", "none", "tbd", "not provided", ""})
_VERIFICATION_ID_PATTERNS = {
    "planning_analysis_decision": re.compile(r"PA-[0-9]+"),
    "development_analysis_decision": re.compile(r"DA-[0-9]+"),
    "policy_remediation_analysis_decision": re.compile(r"PR-[0-9]+"),
}


def _item_texts(document: ParsedDocument, section_name: str) -> list[str]:
    section = document.section(section_name)
    if section is None:
        return []
    return [item.text for item in section.items]


def _finding_target(text: str) -> str | None:
    if "Plan-level:" in text:
        return "plan-level"
    match = _STEP_REFERENCE_PATTERN.search(text)
    return None if match is None else match.group(1)


def _to_content(document: ParsedDocument) -> dict[str, object]:
    if document.frontmatter["type"] == "development_analysis_decision":
        content = _to_content_development(document)
        content["type"] = "development_analysis_decision"
        return content
    shortfall_section = document.section("What Came Up Short")
    shortfall_items = () if shortfall_section is None else shortfall_section.items
    verdict_section = document.section("Criterion Verdicts")
    verdict_items = () if verdict_section is None else verdict_section.items
    how_to_fix = document.section("How To Fix")
    finding_targets: dict[str, str] = {}
    for item in shortfall_items:
        target = _finding_target(item.text)
        if target is not None:
            finding_targets[item.identifier] = target
    if document.frontmatter["type"] == "planning_analysis_decision":
        for item in verdict_items:
            target = _finding_target(item.text)
            if target is not None:
                finding_targets.setdefault(item.identifier, target)
    summary_items = _item_texts(document, "Summary")
    return {
        "status": document.frontmatter["status"],
        "summary": summary_items[0] if summary_items else "",
        "what_came_up_short": [item.text for item in shortfall_items],
        "finding_ids": [item.identifier for item in shortfall_items],
        "finding_targets": finding_targets,
        "criterion_verdicts": [item.text for item in verdict_items],
        "criterion_verdict_ids": [item.identifier for item in verdict_items],
        "how_to_fix": []
        if how_to_fix is None
        else [f"{item.identifier}: {item.text}" for item in how_to_fix.items],
    }


def _to_content_development(document: ParsedDocument) -> dict[str, object]:
    """Map a development analysis decision's body to content.

    The body is free-form below the frontmatter; ``summary`` is the
    first Summary item when present so downstream code (history
    snapshots, displays) keeps something to read. A free-form
    development decision may omit every section.
    """
    summary_items = _item_texts(document, "Summary")
    summary = summary_items[0] if summary_items else ""
    return {
        "status": document.frontmatter["status"],
        "summary": summary,
        "what_came_up_short": [],
        "finding_ids": [],
        "finding_targets": {},
        "criterion_verdicts": [],
        "criterion_verdict_ids": [],
        "how_to_fix": [],
    }


def _normalize(content: dict[str, object]) -> dict[str, object]:
    if (
        content.get("status") == "development_analysis_decision"
        or content.get("type") == "development_analysis_decision"
    ):
        return _normalize_development(content)
    return normalize_analysis_decision_content(content)


def _normalize_development(content: dict[str, object]) -> dict[str, object]:
    """Validate a development decision content dict: status enum only.

    The base ``AnalysisDecision`` model still demands a non-empty summary
    and a status-specific ``what_came_up_short``; bypass both for the
    free-form development contract. The ``type`` field is preserved so
    downstream consumers (history snapshots, displays) keep their
    artifact-type discriminator.
    """
    raw_status = content.get("status")
    if not isinstance(raw_status, str) or raw_status not in _STATUSES:
        raise ValueError(f"status must be one of {list(_STATUSES)!r}")
    return {
        "type": "development_analysis_decision",
        "status": raw_status,
        "summary": content.get("summary", "") or "",
    }


def _validation_diagnostic(item_line: int, section: str, rule_id: str, message: str) -> Diagnostic:
    return Diagnostic(item_line, section, rule_id, message)


def _validate_verification_verdicts(document: ParsedDocument) -> list[Diagnostic]:
    artifact_type = document.frontmatter["type"]
    status = document.frontmatter["status"]
    verdict_section = document.section("Criterion Verdicts")
    verdict_items = () if verdict_section is None else verdict_section.items
    diagnostics: list[Diagnostic] = []
    if not verdict_items:
        diagnostics.append(
            _validation_diagnostic(
                1 if verdict_section is None else verdict_section.line,
                "Criterion Verdicts",
                "ANALYSIS006",
                "verification decisions require a non-empty Criterion Verdicts section",
            )
        )
        return diagnostics

    identifier_pattern = _VERIFICATION_ID_PATTERNS[artifact_type]
    for item in verdict_items:
        if identifier_pattern.fullmatch(item.identifier) is None:
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "Criterion Verdicts",
                    "ANALYSIS010",
                    f"criterion verdict IDs for {artifact_type} must use its numeric phase ID pattern",
                )
            )
        required_fields = (
            _PLANNING_VERDICT_FIELDS
            if artifact_type == "planning_analysis_decision"
            else _REQUIRED_VERDICT_FIELDS
        )
        if any(field not in item.text for field in required_fields):
            missing_cost = "Cost:" in required_fields and "Cost:" not in item.text
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "Criterion Verdicts",
                    "ANALYSIS005",
                    "criterion verdict must include Criterion:, Expected observation:, Verdict:, Evidence:, Location:"
                    + (", and Cost: for planning_analysis_decision" if missing_cost else ""),
                )
            )
            continue
        match = _VERDICT_PATTERN.search(item.text)
        if match is None:
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "Criterion Verdicts",
                    "ANALYSIS008",
                    "criterion verdict must be exactly met, not met, or not evaluable",
                )
            )
            continue
        matched_verdict = match.group(1)
        if not isinstance(matched_verdict, str):
            continue
        verdict = matched_verdict.casefold()
        evidence_match = _EVIDENCE_PATTERN.search(item.text)
        evidence = "" if evidence_match is None else str(evidence_match.group(1)).strip()
        if not evidence:
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "Criterion Verdicts",
                    "ANALYSIS009",
                    "criterion verdict must cite non-empty Evidence:",
                )
            )
        location_match = _LOCATION_PATTERN.search(item.text)
        location = (
            "" if location_match is None else str(location_match.group(1)).strip().rstrip(".")
        )
        if not location:
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "Criterion Verdicts",
                    "ANALYSIS013",
                    "criterion verdict must cite a non-empty Location:",
                )
            )
        if status == "completed" and verdict != "met":
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "Criterion Verdicts",
                    "ANALYSIS012",
                    "a completed verification decision requires every criterion verdict to be 'met'",
                )
            )
        if verdict == "not evaluable" and status != "failed":
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "Criterion Verdicts",
                    "ANALYSIS007",
                    "a 'not evaluable' criterion verdict requires status 'failed'",
                )
            )
        if artifact_type == "planning_analysis_decision" and not _has_finding_target(item.text):
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "Criterion Verdicts",
                    "ANALYSIS004",
                    "planning criterion verdict must identify 'Step: [S-n]', 'Plan-level:', or a 'Plan reference: [<stable id>]'",
                )
            )
        if (
            artifact_type == "planning_analysis_decision"
            and status != "completed"
            and ("Proposed revision:" not in item.text)
        ):
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "Criterion Verdicts",
                    "ANALYSIS019",
                    "planning criterion verdict must include a concrete 'Proposed revision:' the planner can apply or rebut",
                )
            )
    return diagnostics


def _validate_request_changes_predicate(
    what_items: Sequence[ParsedItem],
) -> list[Diagnostic]:
    """Validate that every request_changes finding carries actionable remaining work.

    Each localized shortfall must independently identify a criterion or plan
    reference, a concrete repository location, and non-empty remaining work.
    A single well-formed finding does not redeem a sibling that lacks any
    of the three — every item is checked.
    """
    diagnostics: list[Diagnostic] = []
    # Location in What Came Up Short may be followed by Remaining work:, so
    # stop the capture there rather than consuming the rest of the line.
    loc_re = re.compile(r"Location:\s*(.*?)(?=\s*Remaining work:|$)", re.IGNORECASE)
    for item in what_items:
        remaining_match = _REMAINING_WORK_PATTERN.search(item.text)
        if remaining_match is None:
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "What Came Up Short",
                    "ANALYSIS015",
                    "request_changes finding must include a non-empty 'Remaining work:' statement",
                )
            )
        loc_match = loc_re.search(item.text)
        if loc_match is None:
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "What Came Up Short",
                    "ANALYSIS016",
                    "request_changes finding must include a concrete 'Location:' repository path",
                )
            )
        else:
            location = str(loc_match.group(1)).strip().rstrip(".").casefold()
            if location in _PLACEHOLDER_LOCATIONS:
                diagnostics.append(
                    _validation_diagnostic(
                        item.line,
                        "What Came Up Short",
                        "ANALYSIS016",
                        "request_changes finding Location: must be a concrete repository path, not a placeholder",
                    )
                )
        if "Criterion:" not in item.text and _PLAN_REFERENCE_PATTERN.search(item.text) is None:
            diagnostics.append(
                _validation_diagnostic(
                    item.line,
                    "What Came Up Short",
                    "ANALYSIS017",
                    "request_changes finding must identify 'Criterion:' or a 'Plan reference: [<stable id>]' the plan uses",
                )
            )
    return diagnostics


def _validate_decision_contract(document: ParsedDocument) -> list[Diagnostic]:  # noqa: PLR0911 - structured per-type branches for shared spec
    artifact_type = document.frontmatter["type"]
    status = document.frontmatter["status"]
    if artifact_type == "development_analysis_decision":
        return []
    what_section = document.section("What Came Up Short")
    fix_section = document.section("How To Fix")
    if artifact_type in _VERIFICATION_TYPES:
        diagnostics = _validate_verification_verdicts(document)
        if fix_section is not None:
            diagnostics.append(
                _validation_diagnostic(
                    fix_section.line,
                    "How To Fix",
                    "ANALYSIS011",
                    "verification decisions must omit How To Fix; remedies are inline in the verdict's 'Proposed revision:' (planning) or 'Remaining work:' (development) field on the next pass, so a separate remediation section would split one rule across two places",
                )
            )
    else:
        diagnostics = []

    if status == "completed":
        if what_section is not None:
            diagnostics.append(
                _validation_diagnostic(
                    what_section.line,
                    what_section.name,
                    "ANALYSIS002",
                    "status 'completed' must omit What Came Up Short; known gaps require a non-completed status",
                )
            )
        if artifact_type == "review_analysis_decision" and fix_section is not None:
            diagnostics.append(
                _validation_diagnostic(
                    fix_section.line,
                    fix_section.name,
                    "ANALYSIS002",
                    "status 'completed' must omit both remediation sections; known gaps require a non-completed status",
                )
            )
        return diagnostics

    if status not in {"request_changes", "failed"}:
        return diagnostics
    what_items = () if what_section is None else what_section.items
    diagnostics.extend(
        [
            _validation_diagnostic(
                1 if what_section is None else what_section.line,
                "What Came Up Short",
                "ANALYSIS003",
                "non-completed decisions require a non-empty What Came Up Short section",
            )
        ]
        if not what_items
        else []
    )
    if artifact_type in _VERIFICATION_TYPES:
        diagnostics.extend(
            _validation_diagnostic(
                item.line,
                "What Came Up Short",
                "ANALYSIS005",
                "verification finding must identify Criterion: (or a Plan reference:) and include Expected observation:, Verdict:, Evidence:, and Location:",
            )
            for item in what_items
            if not _finding_fields_complete(item.text)
        )
        diagnostics.extend(
            _validation_diagnostic(
                item.line,
                "What Came Up Short",
                "ANALYSIS007",
                "a 'not evaluable' verification verdict requires status 'failed'",
            )
            for item in what_items
            if "verdict: not evaluable" in item.text.casefold() and status != "failed"
        )
    if artifact_type == "planning_analysis_decision":
        diagnostics.extend(
            _validation_diagnostic(
                item.line,
                "What Came Up Short",
                "ANALYSIS004",
                "planning request_changes finding must identify its affected target as 'Step: [S-n]', 'Plan-level:', or a 'Plan reference: [<stable id>]'",
            )
            for item in what_items
            if not _has_finding_target(item.text)
        )
        diagnostics.extend(
            _validation_diagnostic(
                item.line,
                "What Came Up Short",
                "ANALYSIS019",
                "planning finding must include a concrete 'Proposed revision:' the planner can apply or rebut",
            )
            for item in what_items
            if "Proposed revision:" not in item.text
        )
    if artifact_type in _VERIFICATION_TYPES:
        verdict_section = document.section("Criterion Verdicts")
        if verdict_section is None:
            return diagnostics
        verdict_items = verdict_section.items
        verdict_by_id = {item.identifier: item.text for item in verdict_items}
        shortfall_by_id = {item.identifier: item.text for item in what_items}
        diagnostics.extend(
            _validation_diagnostic(
                item.line,
                "Criterion Verdicts",
                "ANALYSIS014",
                "each non-met criterion verdict must have a matching localized What Came Up Short item",
            )
            for item in verdict_items
            if "verdict: not met" in item.text.casefold()
            and all(field in item.text for field in _REQUIRED_VERDICT_FIELDS)
            and item.identifier not in shortfall_by_id
        )
        diagnostics.extend(
            _validation_diagnostic(
                item.line,
                "What Came Up Short",
                "ANALYSIS014",
                "each What Came Up Short item must mirror a non-met criterion verdict",
            )
            for item in what_items
            if _finding_fields_complete(item.text) and item.identifier not in verdict_by_id
        )
        shortfall_item_by_id = {item.identifier: item for item in what_items}
        diagnostics.extend(
            _validation_diagnostic(
                shortfall_item_by_id[v_item.identifier].line,
                "What Came Up Short",
                "ANALYSIS018",
                "a What Came Up Short item's Verdict must match its mirrored Criterion Verdict",
            )
            for v_item in verdict_items
            if v_item.identifier in shortfall_item_by_id
            and _extract_verdict(v_item.text) is not None
            and _extract_verdict(shortfall_item_by_id[v_item.identifier].text) is not None
            and _extract_verdict(v_item.text)
            != _extract_verdict(shortfall_item_by_id[v_item.identifier].text)
        )
        if artifact_type == "development_analysis_decision" and status == "request_changes":
            diagnostics.extend(_validate_request_changes_predicate(what_items))
        return diagnostics
    if artifact_type != "review_analysis_decision":
        return diagnostics
    fix_items = () if fix_section is None else fix_section.items
    what_ids = {item.identifier for item in what_items}
    fix_ids = {item.identifier for item in fix_items}
    diagnostics.extend(
        _validation_diagnostic(
            item.line,
            "What Came Up Short",
            "ANALYSIS003",
            "What Came Up Short item has no matching How To Fix item",
        )
        for item in what_items
        if item.identifier not in fix_ids
    )
    diagnostics.extend(
        _validation_diagnostic(
            item.line,
            "How To Fix",
            "ANALYSIS003",
            "How To Fix item has no matching What Came Up Short item",
        )
        for item in fix_items
        if item.identifier not in what_ids
    )
    return diagnostics


def _spec(artifact_type: str) -> MdArtifactSpec:
    return MdArtifactSpec(
        artifact_type=artifact_type,
        required_frontmatter=frozenset({"type", "status"}),
        closed_frontmatter={
            "type": FrontmatterVocabulary((artifact_type,), "ANALYSIS001"),
            "status": FrontmatterVocabulary(_STATUSES),
        },
        sections={
            "Summary": SectionRule(require_items=True, max_items=1, allow_body=True),
            "What Came Up Short": SectionRule(required=False, allow_body=True),
            "Criterion Verdicts": SectionRule(required=False, allow_body=True),
            "How To Fix": SectionRule(required=False, allow_body=True),
        },
        to_content=_to_content,
        normalize_content=_normalize,
        validate_document=_validate_decision_contract,
        allow_unknown_frontmatter=True,
        allow_unknown_sections=True,
    )


def _spec_development() -> MdArtifactSpec:
    """Development analysis decision: status-only validation.

    The body is free-form prose the next agent reads. The validator
    only checks the frontmatter ``status`` enum. Section rules, body
    grammar rules, and per-finding field labels all belong to the
    structured planning / review / policy contracts and are not
    applied here.
    """
    return MdArtifactSpec(
        artifact_type="development_analysis_decision",
        required_frontmatter=frozenset({"type", "status"}),
        closed_frontmatter={
            "type": FrontmatterVocabulary(("development_analysis_decision",), "ANALYSIS001"),
            "status": FrontmatterVocabulary(_STATUSES),
        },
        sections={},
        to_content=_to_content,
        normalize_content=_normalize,
        allow_unknown_frontmatter=True,
        allow_unknown_sections=True,
        structured_body=lambda document: False,  # body is always free-form
    )


ANALYSIS_DECISION_SPECS = tuple(
    _spec_development()
    if artifact_type == "development_analysis_decision"
    else _spec(artifact_type)
    for artifact_type in _ANALYSIS_TYPES
)

for _specification in ANALYSIS_DECISION_SPECS:
    register_spec(_specification)


__all__ = ["ANALYSIS_DECISION_SPECS"]
