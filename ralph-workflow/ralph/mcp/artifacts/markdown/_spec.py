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
type MinimalVariantParser = Callable[[ParsedDocument], tuple[Content | None, list[Diagnostic]]]


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


def parse_and_validate(text: str, spec: MdArtifactSpec) -> tuple[Content, list[Diagnostic]]:
    """Parse and validate markdown through one shared, pure artifact gate."""
    document, diagnostics = parse_markdown_document(
        text,
        allow_nested_headings=spec.allow_nested_headings,
    )
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
    if not _has_errors(diagnostics):
        try:
            content = minimal_content if minimal_content is not None else spec.to_content(document)
            normalized = spec.normalize_content(content)
        except MarkdownArtifactError as exc:
            diagnostics.extend(exc.diagnostics)
            return {}, diagnostics
        except (TypeError, ValueError) as exc:
            diagnostics.append(_normalizer_diagnostic(document, str(exc)))
            return {}, diagnostics
        return normalized, diagnostics
    return {}, diagnostics


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
        return (
            f"missing required frontmatter {key!r}; blocking because {hint}"
        )
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

    Every MD006 message starts as ``duplicate frontmatter field <key>``; the
    teach pass appends the consumer phrase (closed-vocabulary acceptance
    list when one is registered, otherwise the spec-registry phrase for the
    plain ``type`` frontmatter field) plus a ``resolve by`` clause so the
    diagnostic reads ``what; blocking because <consumer>; resolve by <fix>``.
    """
    for index, diagnostic in enumerate(diagnostics):
        if diagnostic.rule_id != "MD006":
            continue
        for field_name, vocabulary in spec.closed_frontmatter.items():
            if diagnostic.message != f"duplicate frontmatter field {field_name!r}":
                continue
            accepted = ", ".join(vocabulary.values)
            diagnostics[index] = Diagnostic(
                diagnostic.line,
                diagnostic.section,
                diagnostic.rule_id,
                f"{diagnostic.message}; blocking because the closed vocabulary for "
                f"{field_name!r} is consumed by the spec registry "
                "(ralph/mcp/artifacts/markdown/registry.py) so duplicates cannot be "
                f"routed; resolve by keeping exactly one {field_name!r} field "
                f"whose value is one of: {accepted}",
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
                f"{diagnostic.message}; blocking because the artifact spec registry "
                "(ralph/mcp/artifacts/markdown/registry.py) routes the parsed "
                f"{field_name} value to the plan validator and a duplicate field "
                f"cannot be routed; resolve by keeping exactly one {field_name} field",
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


def _normalizer_diagnostic(document: ParsedDocument, message: str) -> Diagnostic:
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
        _spec010_message(message or "canonical validation failed"),
    )


def _spec010_message(message: str) -> str:
    """Wrap a pydantic / size / normalizer rejection in the consumer convention.

    The plan spec normalizer can fail in three ways: a pydantic schema
    rejection, a ``plan size violation`` from the canonical plan payload
    bound, or any other TypeError/ValueError surfaced by the normalizer
    callable. Each one ends with ``; blocking because <consumer>; resolve
    by <fix>`` so the diagnostic matches the convention every other
    blocking finding follows.
    """
    what = message.strip() or "canonical validation failed"
    if what.lower().startswith("plan size violation"):
        return (
            f"{what}; blocking because the plan is carried through MCP tool result "
            "payloads and unbounded documents exceed the bounded payload contract; "
            "resolve by reducing the plan to its essential steps and verification"
        )
    return (
        f"{what}; blocking because ralph/mcp/artifacts/plan/_validation.py "
        "enforces pydantic field schemas on the canonical plan content dict; "
        "resolve by correcting the rejected field against its pydantic schema"
    )


def _has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(diagnostic.severity == "error" for diagnostic in diagnostics)


__all__ = ["MdArtifactSpec", "SectionRule", "parse_and_validate"]
