"""Shared closed-grammar validation entry point for markdown artifacts."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown._artifact_error import MarkdownArtifactError
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._document import ParsedDocument
from ralph.mcp.artifacts.markdown._parser import parse_markdown_document, stray_line_diagnostic
from ralph.mcp.artifacts.markdown._references import validate_unique_ids
from ralph.mcp.artifacts.markdown._section_rule import SectionRule

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._frontmatter_vocabulary import FrontmatterVocabulary
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection

type Content = dict[str, object]
type DocumentPredicate = Callable[[ParsedDocument], bool]
type DocumentMapper = Callable[[ParsedDocument], Content]
type ContentNormalizer = Callable[[Content], Content]
type DocumentValidator = Callable[[ParsedDocument], list[Diagnostic]]
type TextValidator = Callable[[str], list[Diagnostic]]
type MinimalVariantParser = Callable[[ParsedDocument], tuple[Content | None, list[Diagnostic]]]
type SeverityPolicy = Callable[[list[Diagnostic]], None]


_BODY_GRAMMAR_RULES = frozenset({"MD001", "MD002", "MD003", "MD004"})


@dataclass(frozen=True)
class MdArtifactSpec:
    """Declarative schema and injected canonical validator for one artifact type.

    ``structured_body`` decides, per document, whether the body must
    satisfy this spec's section grammar. When it returns False the body
    is free-form prose: section rules, the spec's ``validate_document``
    hook, and body markdown grammar are all skipped, while frontmatter
    rules (required fields, closed vocabularies, duplicates) still
    apply. Leaving it unset keeps every document structured.
    """

    artifact_type: str
    required_frontmatter: frozenset[str]
    sections: Mapping[str, SectionRule]
    to_content: DocumentMapper
    normalize_content: ContentNormalizer
    optional_frontmatter: frozenset[str] = frozenset()
    required_frontmatter_hints: Mapping[str, str] = field(default_factory=dict)
    validate_document: DocumentValidator | None = None
    minimal_variant: MinimalVariantParser | None = None
    max_characters: int | None = None
    unknown_section_rule: SectionRule | None = None
    closed_frontmatter: Mapping[str, FrontmatterVocabulary] = field(default_factory=dict)
    allow_unknown_frontmatter: bool = False
    allow_unknown_sections: bool = False
    allow_nested_headings: bool = False
    structured_body: DocumentPredicate | None = None
    validate_text: TextValidator | None = None
    severity_policy: SeverityPolicy | None = None


def parse_and_validate(text: str, spec: MdArtifactSpec) -> tuple[Content, list[Diagnostic]]:
    """Parse and validate markdown through one shared, pure artifact gate."""
    document, diagnostics = parse_markdown_document(
        text,
        allow_nested_headings=spec.allow_nested_headings,
    )
    if spec.validate_text is not None:
        # Spec-level text validators run after the parser so the document
        # state is available, but before structure validation so a
        # text-level rejection short-circuits before any other check
        # fires. The plan spec uses this hook for the closed-list
        # not-a-plan detector; other specs leave it unset so this
        # branch is a no-op for them.
        diagnostics.extend(spec.validate_text(text))
    _teach_duplicate_closed_frontmatter_vocabulary(diagnostics, spec)
    structured_body = spec.structured_body is None or spec.structured_body(document)
    if not structured_body:
        diagnostics = [
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.rule_id not in _BODY_GRAMMAR_RULES
        ]
    minimal_content: Content | None = None
    if spec.minimal_variant is not None:
        minimal_content, variant_diagnostics = spec.minimal_variant(document)
        diagnostics.extend(variant_diagnostics)
    diagnostics.extend(
        _validate_structure(
            document,
            text,
            spec,
            require_sections=minimal_content is None,
            validate_sections=structured_body,
        )
    )
    if (
        not _has_errors(diagnostics)
        and spec.validate_document is not None
        and minimal_content is None
        and structured_body
    ):
        diagnostics.extend(spec.validate_document(document))
    if spec.severity_policy is not None:
        # The plan-scoped policy demotes content-shape findings (PLAN021,
        # PLAN022, REF001-004, the pydantic branch of SPEC010, ...) from
        # error to warning. Applying it after every diagnostic has been
        # gathered (including the pydantic-side SPEC010 raised below) keeps
        # the advisory-wearing-an-error-label failure mode the brief is
        # designed to prevent closed off in this code path.
        spec.severity_policy(diagnostics)
    if not _has_errors(diagnostics):
        try:
            raw_content = (
                minimal_content if minimal_content is not None else spec.to_content(document)
            )
            return spec.normalize_content(raw_content), diagnostics
        except MarkdownArtifactError as exc:
            diagnostics.extend(exc.diagnostics)
            if spec.severity_policy is not None:
                spec.severity_policy(diagnostics)
            # Best-effort: if the markdown mapper's diagnostics were all
            # demoted to warning, persist the raw mapped content so a
            # warnings-only plan still reaches downstream consumers.
            if not _has_errors(diagnostics) and minimal_content is None:
                return _best_effort_content(spec, document, diagnostics)
            return {}, diagnostics
        except (TypeError, ValueError) as exc:
            diagnostics.append(_normalizer_diagnostic(document, str(exc), spec.artifact_type))
            if spec.severity_policy is not None:
                spec.severity_policy(diagnostics)
            # Best-effort: if the pydantic-side SPEC010 was demoted to
            # warning, persist the raw mapped content so consumers still
            # see what the markdown side produced.
            if not _has_errors(diagnostics) and minimal_content is None:
                return _best_effort_content(spec, document, diagnostics)
            return {}, diagnostics
    return {}, diagnostics


def _best_effort_content(
    spec: MdArtifactSpec,
    document: ParsedDocument,
    diagnostics: list[Diagnostic],
) -> tuple[Content, list[Diagnostic]]:
    """Return the raw mapped content when only warnings remain.

    Used when the canonical normalizer raised a finding the plan-scoped
    policy has demoted to warning: the document has no blocking errors
    but its canonical content fails pydantic or section-shape. Consumers
    (development_result proof readers, work-unit dispatch) are defensive
    about partial content; persisting the raw mapped dict keeps the
    warnings-only document visible to them instead of collapsing it to
    ``{}`` and forcing the agent to lose the planning substance to a
    shape complaint.
    """
    try:
        raw_content = spec.to_content(document)
    except MarkdownArtifactError as exc:
        for diagnostic in exc.diagnostics:
            if diagnostic not in diagnostics:
                diagnostics.append(diagnostic)
        if spec.severity_policy is not None:
            spec.severity_policy(diagnostics)
        if not _has_errors(diagnostics):
            return {}, diagnostics
        return {}, diagnostics
    return raw_content, diagnostics


def _validate_structure(
    document: ParsedDocument,
    text: str,
    spec: MdArtifactSpec,
    *,
    require_sections: bool,
    validate_sections: bool = True,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if spec.max_characters is not None and len(text) > spec.max_characters:
        diagnostics.append(Diagnostic(1, None, "SPEC001", "document exceeds its character limit"))
    allowed_frontmatter = (
        spec.required_frontmatter | spec.optional_frontmatter | frozenset(spec.closed_frontmatter)
    )
    diagnostics.extend(
        Diagnostic(
            1,
            None,
            "SPEC002",
            _missing_frontmatter_message(spec, key),
        )
        for key in spec.required_frontmatter
        if key not in document.frontmatter
    )
    diagnostics.extend(_validate_closed_frontmatter(document, spec))
    for key, line in document.frontmatter_lines.items():
        if key not in allowed_frontmatter and not spec.allow_unknown_frontmatter:
            diagnostics.append(
                Diagnostic(line, None, "SPEC003", f"unknown frontmatter field {key!r}")
            )
    if not validate_sections:
        return diagnostics
    seen_sections: set[str] = set()
    for section in document.sections:
        rule = spec.sections.get(section.name)
        if rule is None:
            if spec.allow_unknown_sections:
                continue
            if spec.unknown_section_rule is None:
                diagnostics.append(
                    Diagnostic(section.line, section.name, "SPEC004", "unknown section")
                )
                continue
            rule = spec.unknown_section_rule
        if section.name in seen_sections and not rule.repeatable:
            diagnostics.append(
                Diagnostic(section.line, section.name, "SPEC005", "duplicate section")
            )
        seen_sections.add(section.name)
        if rule.require_items and not section.items:
            diagnostics.append(
                Diagnostic(section.line, section.name, "SPEC006", "section requires list items")
            )
        if rule.max_items is not None and len(section.items) > rule.max_items:
            diagnostics.append(
                Diagnostic(section.line, section.name, "SPEC007", "section exceeds its item limit")
            )
        diagnostics.extend(
            validate_unique_ids(
                section.items, section=section.name, case_sensitive=rule.case_sensitive_ids
            )
        )
        diagnostics.extend(_validate_section_shapes(section, rule))
    for name, rule in spec.sections.items():
        if require_sections and rule.required and name not in seen_sections:
            diagnostics.append(Diagnostic(1, name, "SPEC008", f"missing required section {name!r}"))
    return diagnostics


def _missing_frontmatter_message(spec: MdArtifactSpec, key: str) -> str:
    vocabulary = spec.closed_frontmatter.get(key)
    if vocabulary is not None:
        accepted = ", ".join(vocabulary.values)
        return (
            f"missing required frontmatter {key!r}; blocking because the closed "
            f"vocabulary for {key!r} is consumed by the spec registry "
            "(ralph/mcp/artifacts/markdown/registry.py) and the value cannot be "
            f"inferred; resolve by setting {key!r} to one of: {accepted}"
        )
    hint = spec.required_frontmatter_hints.get(key)
    if hint is not None:
        return f"missing required frontmatter {key!r}; blocking because {hint}"
    return (
        f"missing required frontmatter {key!r}; blocking because the artifact "
        "spec registry (ralph/mcp/artifacts/markdown/registry.py) routes the "
        f"parsed {key!r} value to a validator and a missing field cannot be "
        f"routed; resolve by adding a {key!r} field whose value names this "
        "artifact's canonical type"
    )


def _teach_duplicate_closed_frontmatter_vocabulary(
    diagnostics: list[Diagnostic],
    spec: MdArtifactSpec,
) -> None:
    """Rewrite MD006 duplicate-frontmatter diagnostics into the consumer convention.

    The parser already attaches a generic routing-consumer phrase to every
    MD006 message; when the duplicated field has a closed vocabulary in
    the spec, the teach pass replaces the generic ``resolve by`` line with
    the closed-vocabulary acceptance list so the agent sees the exact
    set of values the consumer can route. Plain ``type`` duplicates and
    other non-closed fields keep the generic spec-registry phrase.

    Detection: the message starts with ``duplicate frontmatter field <key>``
    (followed by the parser's appended consumer phrase), so the prefix
    regex anchors the look-up to the start of the message.
    """
    for index, diagnostic in enumerate(diagnostics):
        if diagnostic.rule_id != "MD006":
            continue
        for field_name, vocabulary in spec.closed_frontmatter.items():
            if not diagnostic.message.startswith(f"duplicate frontmatter field {field_name!r}"):
                continue
            accepted = ", ".join(vocabulary.values)
            diagnostics[index] = Diagnostic(
                diagnostic.line,
                diagnostic.section,
                diagnostic.rule_id,
                f"duplicate frontmatter field {field_name!r}; blocking because the "
                f"closed vocabulary for {field_name!r} is consumed by the spec "
                "registry (ralph/mcp/artifacts/markdown/registry.py) so "
                f"duplicates cannot be routed; resolve by keeping exactly one "
                f"{field_name!r} field whose value is one of: {accepted}",
                diagnostic.severity,
            )
            break
        else:
            # Plain ``type`` (or any other non-closed field with a hint):
            # name the spec-registry consumer without inventing a vocabulary.
            field_match = re.match(r"duplicate frontmatter field ('.*?')", diagnostic.message)
            if field_match is None:
                continue
            field_name = field_match.group(1)
            diagnostics[index] = Diagnostic(
                diagnostic.line,
                diagnostic.section,
                diagnostic.rule_id,
                f"duplicate frontmatter field {field_name!r}; blocking because the "
                "artifact spec registry (ralph/mcp/artifacts/markdown/registry.py) "
                f"routes the parsed {field_name} value to the plan validator and "
                f"a duplicate field cannot be routed; resolve by keeping exactly "
                f"one {field_name} field",
                diagnostic.severity,
            )


def _validate_closed_frontmatter(
    document: ParsedDocument, spec: MdArtifactSpec
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for field_name, vocabulary in spec.closed_frontmatter.items():
        value = document.frontmatter.get(field_name)
        if value is None or value in vocabulary.values:
            continue
        accepted = ", ".join(vocabulary.values)
        diagnostics.append(
            Diagnostic(
                document.frontmatter_lines[field_name],
                None,
                vocabulary.rule_id,
                f"frontmatter {field_name!r} must be one of: {accepted}",
            )
        )
    return diagnostics


def _validate_section_shapes(section: ParsedSection, rule: SectionRule) -> list[Diagnostic]:
    """Enforce which content shapes (body, items, blocks) one section admits."""
    diagnostics: list[Diagnostic] = []
    if rule.allow_blocks:
        diagnostics.extend(
            validate_unique_ids(
                section.blocks, section=section.name, case_sensitive=rule.case_sensitive_ids
            )
        )
        if not rule.items_allowed:
            diagnostics.extend(
                Diagnostic(
                    item.line,
                    section.name,
                    "SPEC011",
                    "section content must be '### [ID] Title' blocks",
                )
                for item in section.items
            )
    else:
        diagnostics.extend(
            Diagnostic(
                block.line,
                section.name,
                "MD001",
                "headings must use '## Section' or '### [ID] Title'",
            )
            for block in section.blocks
        )
    if rule.require_blocks and not section.blocks:
        diagnostics.append(
            Diagnostic(
                section.line,
                section.name,
                "SPEC012",
                "section requires at least one '### [ID] Title' block",
            )
        )
    if not rule.allow_body:
        diagnostics.extend(stray_line_diagnostic(line, section.name) for line in section.lines)
        diagnostics.extend(
            stray_line_diagnostic(field_line, section.name)
            for item in section.items
            for field_line in item.fields
        )
    return diagnostics


def _normalizer_diagnostic(
    document: ParsedDocument, message: str, artifact_type: str
) -> Diagnostic:
    field_name = message.split(" ", 1)[0].split(".", 1)[0]
    line = document.frontmatter_lines.get(field_name, 1)
    section = next(
        (
            section.name
            for section in document.sections
            if section.name.casefold() == field_name.casefold()
        ),
        None,
    )
    return Diagnostic(
        line,
        section,
        "SPEC010",
        _spec010_message(message or "canonical validation failed", artifact_type),
    )


def _spec010_message(message: str, artifact_type: str) -> str:
    """Wrap a pydantic / size / normalizer rejection in the consumer convention.

    A spec normalizer can fail in three ways: a pydantic schema rejection,
    a ``plan size violation`` from the canonical plan payload bound, or any
    other TypeError/ValueError surfaced by the normalizer callable. Each one
    ends with ``; blocking because <consumer>; resolve by <fix>`` so the
    diagnostic matches the convention every other blocking finding follows.
    The consumer clause names the artifact type that actually rejected the
    document: attributing every artifact's rejection to the plan pydantic
    schema sends the agent to read a validator that never saw its payload.
    """
    what = message.strip() or "canonical validation failed"
    if what.lower().startswith("plan size violation"):
        return (
            f"{what}; blocking because the plan is carried through MCP tool result "
            "payloads and unbounded documents exceed the bounded payload contract; "
            "resolve by reducing the plan to its essential steps and verification"
        )
    if artifact_type == "plan":
        return (
            f"{what}; blocking because ralph/mcp/artifacts/plan/_validation.py "
            "enforces pydantic field schemas on the canonical plan content dict; "
            "resolve by correcting the rejected field against its pydantic schema"
        )
    return (
        f"{what}; blocking because the canonical {artifact_type} normalizer rejects "
        "the document before it is stored, so the artifact was not accepted; "
        "resolve by applying the correction named above and resubmitting"
    )


def _has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(diagnostic.severity == "error" for diagnostic in diagnostics)


__all__ = ["MdArtifactSpec", "SectionRule", "parse_and_validate"]
