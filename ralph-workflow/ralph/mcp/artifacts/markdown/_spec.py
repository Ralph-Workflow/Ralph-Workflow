"""Shared closed-grammar validation entry point for markdown artifacts."""

from __future__ import annotations

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
type DocumentMapper = Callable[[ParsedDocument], Content]
type ContentNormalizer = Callable[[Content], Content]
type DocumentValidator = Callable[[ParsedDocument], list[Diagnostic]]
type MinimalVariantParser = Callable[[ParsedDocument], tuple[Content | None, list[Diagnostic]]]


@dataclass(frozen=True)
class MdArtifactSpec:
    """Declarative schema and injected canonical validator for one artifact type."""

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


def parse_and_validate(text: str, spec: MdArtifactSpec) -> tuple[Content, list[Diagnostic]]:
    """Parse and validate markdown through one shared, pure artifact gate."""
    document, diagnostics = parse_markdown_document(
        text,
        allow_nested_headings=spec.allow_nested_headings,
    )
    _teach_duplicate_closed_frontmatter_vocabulary(diagnostics, spec)
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
        )
    )
    if (
        not _has_errors(diagnostics)
        and spec.validate_document is not None
        and minimal_content is None
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
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if spec.max_characters is not None and len(text) > spec.max_characters:
        diagnostics.append(Diagnostic(1, None, "SPEC001", "document exceeds its character limit"))
    allowed_frontmatter = (
        spec.required_frontmatter
        | spec.optional_frontmatter
        | frozenset(spec.closed_frontmatter)
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
        return f"missing required frontmatter {key!r}; accepted values are: {accepted}"
    return spec.required_frontmatter_hints.get(key, f"missing required frontmatter {key!r}")


def _teach_duplicate_closed_frontmatter_vocabulary(
    diagnostics: list[Diagnostic],
    spec: MdArtifactSpec,
) -> None:
    """Make duplicate consumed-field errors name the accepted vocabulary."""
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
                f"{diagnostic.message}; keep exactly one {field_name!r} field "
                f"whose value is one of: {accepted}",
                diagnostic.severity,
            )
            break


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
    return Diagnostic(line, section, "SPEC010", message or "canonical validation failed")


def _has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(diagnostic.severity == "error" for diagnostic in diagnostics)


__all__ = ["MdArtifactSpec", "SectionRule", "parse_and_validate"]
