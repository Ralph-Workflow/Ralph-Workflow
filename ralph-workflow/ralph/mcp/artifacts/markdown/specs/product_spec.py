"""Markdown spec for product specifications."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown import MdArtifactSpec, SectionRule
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.product_spec import normalize_product_spec_content

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument


def _item_texts(document: ParsedDocument, section_name: str) -> list[str]:
    section = document.section(section_name)
    return [] if section is None else [item.text for item in section.items]


def _to_content(document: ParsedDocument) -> dict[str, object]:
    title = _item_texts(document, "Title")
    scope = _item_texts(document, "Scope")
    return {
        "title": title[0],
        "scope": scope[0],
        "goals": _item_texts(document, "Goals"),
        "users": _item_texts(document, "Users"),
        "constraints": _item_texts(document, "Constraints"),
        "success_criteria": _item_texts(document, "Success Criteria"),
        "product_behavior": _item_texts(document, "Product Behavior"),
        "ux_ui_requirements": _item_texts(document, "UX UI Requirements"),
        "scope_boundaries": _item_texts(document, "Scope Boundaries"),
        "open_questions": _item_texts(document, "Open Questions"),
    }


def _validate_type(document: ParsedDocument) -> list[Diagnostic]:
    if document.frontmatter["type"] == "product_spec":
        return []
    return [
        Diagnostic(
            document.frontmatter_lines["type"],
            None,
            "PRODUCT001",
            "frontmatter 'type' must be 'product_spec'",
        )
    ]


def _normalize(content: dict[str, object]) -> dict[str, object]:
    return normalize_product_spec_content(content)


PRODUCT_SPEC = MdArtifactSpec(
    artifact_type="product_spec",
    required_frontmatter=frozenset({"type"}),
    sections={
        "Title": SectionRule(require_items=True, max_items=1),
        "Scope": SectionRule(require_items=True, max_items=1),
        "Goals": SectionRule(require_items=True),
        "Users": SectionRule(require_items=True),
        "Constraints": SectionRule(required=False),
        "Success Criteria": SectionRule(require_items=True),
        "Product Behavior": SectionRule(required=False),
        "UX UI Requirements": SectionRule(required=False),
        "Scope Boundaries": SectionRule(required=False),
        "Open Questions": SectionRule(required=False),
    },
    to_content=_to_content,
    normalize_content=_normalize,
    validate_document=_validate_type,
)

register_spec(PRODUCT_SPEC)

__all__ = ["PRODUCT_SPEC"]
