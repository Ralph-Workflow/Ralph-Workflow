"""Markdown spec for smoke-test results."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown import LenientEnum, MdArtifactSpec, SectionRule
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.smoke_test_result import normalize_smoke_test_result_content

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument


def _item_texts(document: ParsedDocument, section_name: str) -> list[str]:
    section = document.section(section_name)
    return [] if section is None else [item.text for item in section.items]


def _to_content(document: ParsedDocument) -> dict[str, object]:
    summary = _item_texts(document, "Summary")
    return {
        "status": document.frontmatter["status"],
        "summary": summary[0],
        "output_file": document.frontmatter["output_file"],
        "observed_working": _item_texts(document, "Observed Working"),
        "observed_breaks": _item_texts(document, "Observed Breaks"),
        "headless_guide_checks": _item_texts(document, "Headless Guide Checks"),
    }


def _validate_type(document: ParsedDocument) -> list[Diagnostic]:
    if document.frontmatter["type"] == "smoke_test_result":
        return []
    return [
        Diagnostic(
            document.frontmatter_lines["type"],
            None,
            "SMOKE001",
            "frontmatter 'type' must be 'smoke_test_result'",
        )
    ]


def _normalize(content: dict[str, object]) -> dict[str, object]:
    return normalize_smoke_test_result_content(content)


SMOKE_TEST_RESULT_SPEC = MdArtifactSpec(
    artifact_type="smoke_test_result",
    required_frontmatter=frozenset({"type", "status", "output_file"}),
    sections={
        "Summary": SectionRule(require_items=True, max_items=1),
        "Observed Working": SectionRule(required=False),
        "Observed Breaks": SectionRule(required=False),
        "Headless Guide Checks": SectionRule(require_items=True),
    },
    to_content=_to_content,
    normalize_content=_normalize,
    lenient_enums={
        "status": LenientEnum(frozenset({"passed", "failed", "partial"}), "partial"),
    },
    validate_document=_validate_type,
)

register_spec(SMOKE_TEST_RESULT_SPEC)

__all__ = ["SMOKE_TEST_RESULT_SPEC"]
