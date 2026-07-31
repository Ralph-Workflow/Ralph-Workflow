"""Markdown specification for a bounded, comparable run-time report."""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import MdArtifactSpec, SectionRule
from ralph.mcp.artifacts.markdown._frontmatter_vocabulary import FrontmatterVocabulary
from ralph.mcp.artifacts.markdown.registry import register_spec


def _content(_: object) -> dict[str, object]:
    return {}


RUN_TIME_REPORT_SPEC = MdArtifactSpec(
    artifact_type="run_time_report",
    required_frontmatter=frozenset({"type", "outcome", "elapsed_seconds", "final_phase"}),
    closed_frontmatter={"type": FrontmatterVocabulary(("run_time_report",), "RTR001")},
    sections={
        "Summary": SectionRule(require_items=True, max_items=1, allow_body=True),
        "Timing": SectionRule(require_items=True, allow_body=True),
        "Phases": SectionRule(require_items=True, allow_body=True),
        "Slowest Steps": SectionRule(require_items=True, allow_body=True),
        "Memory Findings": SectionRule(
            required=False, require_items=False, allow_body=False
        ),
        "Signals": SectionRule(require_items=True, allow_body=True),
    },
    to_content=_content,
    normalize_content=lambda content: content,
    allow_unknown_frontmatter=False,
    allow_unknown_sections=False,
    max_characters=1_600,
)

register_spec(RUN_TIME_REPORT_SPEC)

__all__ = ["RUN_TIME_REPORT_SPEC"]
