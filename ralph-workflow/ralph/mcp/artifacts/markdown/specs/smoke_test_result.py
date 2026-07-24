"""Markdown spec for smoke-test results.

Consumed structure (stays strict): frontmatter ``type``, ``status``
(closed vocabulary ``passed`` | ``failed`` | ``partial`` — a wrong
status is a hard error naming the valid values), ``output_file``, and
the required-section skeleton. Section bodies are descriptive and
tolerate multi-line prose and unknown continuation lines under items.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown import MdArtifactSpec, SectionRule
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.smoke_test_result import normalize_smoke_test_result_content

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument

_STATUSES = ("passed", "failed", "partial")


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


def _validate_frontmatter(document: ParsedDocument) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if document.frontmatter["type"] != "smoke_test_result":
        diagnostics.append(
            Diagnostic(
                document.frontmatter_lines["type"],
                None,
                "SMOKE001",
                "frontmatter 'type' must be 'smoke_test_result'",
            )
        )
    if document.frontmatter["status"] not in _STATUSES:
        diagnostics.append(
            Diagnostic(
                document.frontmatter_lines["status"],
                None,
                "SPEC010",
                "frontmatter 'status' must be one of: passed, failed, partial",
            )
        )
    return diagnostics


def _normalize(content: dict[str, object]) -> dict[str, object]:
    return normalize_smoke_test_result_content(content)


SMOKE_TEST_RESULT_SPEC = MdArtifactSpec(
    artifact_type="smoke_test_result",
    required_frontmatter=frozenset({"type", "status", "output_file"}),
    sections={
        "Summary": SectionRule(require_items=True, max_items=1, allow_body=True),
        "Observed Working": SectionRule(required=False, allow_body=True),
        "Observed Breaks": SectionRule(required=False, allow_body=True),
        "Headless Guide Checks": SectionRule(require_items=True, allow_body=True),
    },
    to_content=_to_content,
    normalize_content=_normalize,
    validate_frontmatter=_validate_frontmatter,
)

register_spec(SMOKE_TEST_RESULT_SPEC)

__all__ = ["SMOKE_TEST_RESULT_SPEC"]
