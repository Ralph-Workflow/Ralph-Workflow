"""Shared closed-grammar validation entry point for markdown artifacts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._document import ParsedDocument
from ralph.mcp.artifacts.markdown._parser import parse_markdown_document
from ralph.mcp.artifacts.markdown._references import validate_unique_ids

type Content = dict[str, object]
type DocumentMapper = Callable[[ParsedDocument], Content]
type ContentNormalizer = Callable[[Content], Content]
type DocumentValidator = Callable[[ParsedDocument], list[Diagnostic]]


@dataclass(frozen=True)
class LenientEnum:
    """A frontmatter token that may be safely coerced to a documented default."""

    allowed: frozenset[str]
    default: str


@dataclass(frozen=True)
class SectionRule:
    """Closed grammar rules for one named section."""

    required: bool = True
    require_items: bool = False
    max_items: int | None = None
    case_sensitive_ids: bool = True


@dataclass(frozen=True)
class MdArtifactSpec:
    """Declarative schema and injected canonical validator for one artifact type."""

    artifact_type: str
    required_frontmatter: frozenset[str]
    sections: Mapping[str, SectionRule]
    to_content: DocumentMapper
    normalize_content: ContentNormalizer
    optional_frontmatter: frozenset[str] = frozenset()
    lenient_enums: Mapping[str, LenientEnum] = field(default_factory=dict)
    validate_document: DocumentValidator | None = None
    max_characters: int | None = None


def parse_and_validate(text: str, spec: MdArtifactSpec) -> tuple[Content, list[Diagnostic]]:
    """Parse and validate markdown through one shared, pure artifact gate.

    Error diagnostics make the returned content unsuitable for submission;
    warnings document intentional vocabulary coercion and do not block it.
    """
    document, diagnostics = parse_markdown_document(text)
    diagnostics.extend(_validate_structure(document, text, spec))
    document = _coerce_lenient_frontmatter(document, spec, diagnostics)
    if not _has_errors(diagnostics):
        try:
            content = spec.to_content(document)
            normalized = spec.normalize_content(content)
        except (TypeError, ValueError) as exc:
            diagnostics.append(_normalizer_diagnostic(document, str(exc)))
            return {}, diagnostics
        if spec.validate_document is not None:
            diagnostics.extend(spec.validate_document(document))
        return normalized, diagnostics
    return {}, diagnostics


def _validate_structure(
    document: ParsedDocument, text: str, spec: MdArtifactSpec
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if spec.max_characters is not None and len(text) > spec.max_characters:
        diagnostics.append(Diagnostic(1, None, "SPEC001", "document exceeds its character limit"))
    allowed_frontmatter = spec.required_frontmatter | spec.optional_frontmatter | frozenset(
        spec.lenient_enums
    )
    diagnostics.extend(
        Diagnostic(1, None, "SPEC002", f"missing required frontmatter {key!r}")
        for key in spec.required_frontmatter
        if key not in document.frontmatter
    )
    for key, line in document.frontmatter_lines.items():
        if key not in allowed_frontmatter:
            diagnostics.append(Diagnostic(line, None, "SPEC003", f"unknown frontmatter field {key!r}"))
    seen_sections: set[str] = set()
    for section in document.sections:
        rule = spec.sections.get(section.name)
        if rule is None:
            diagnostics.append(Diagnostic(section.line, section.name, "SPEC004", "unknown section"))
            continue
        if section.name in seen_sections:
            diagnostics.append(Diagnostic(section.line, section.name, "SPEC005", "duplicate section"))
        seen_sections.add(section.name)
        if rule.require_items and not section.items:
            diagnostics.append(Diagnostic(section.line, section.name, "SPEC006", "section requires list items"))
        if rule.max_items is not None and len(section.items) > rule.max_items:
            diagnostics.append(Diagnostic(section.line, section.name, "SPEC007", "section exceeds its item limit"))
        diagnostics.extend(
            validate_unique_ids(
                section.items, section=section.name, case_sensitive=rule.case_sensitive_ids
            )
        )
    for name, rule in spec.sections.items():
        if rule.required and name not in seen_sections:
            diagnostics.append(Diagnostic(1, name, "SPEC008", f"missing required section {name!r}"))
    return diagnostics


def _coerce_lenient_frontmatter(
    document: ParsedDocument, spec: MdArtifactSpec, diagnostics: list[Diagnostic]
) -> ParsedDocument:
    frontmatter = dict(document.frontmatter)
    for field_name, rule in spec.lenient_enums.items():
        value = frontmatter.get(field_name)
        if value is not None and value not in rule.allowed:
            frontmatter[field_name] = rule.default
            diagnostics.append(
                Diagnostic(
                    document.frontmatter_lines[field_name],
                    None,
                    "SPEC009",
                    f"{field_name!r} value {value!r} coerced to {rule.default!r}",
                    "warning",
                )
            )
    return ParsedDocument(frontmatter, document.frontmatter_lines, document.sections)


def _normalizer_diagnostic(document: ParsedDocument, message: str) -> Diagnostic:
    field_name = message.split(" ", 1)[0].split(".", 1)[0]
    line = document.frontmatter_lines.get(field_name, 1)
    section = next(
        (section.name for section in document.sections if section.name.casefold() == field_name.casefold()),
        None,
    )
    return Diagnostic(line, section, "SPEC010", message or "canonical validation failed")


def _has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(diagnostic.severity == "error" for diagnostic in diagnostics)


__all__ = ["LenientEnum", "MdArtifactSpec", "SectionRule", "parse_and_validate"]
