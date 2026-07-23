"""The new ``## Agent-Driven Parallel Execution`` heading must be present
in ``planning.jinja`` and the old
``## Same-Workspace Parallel Worker Rules`` heading must be absent.

This test reads the template source directly (rather than rendering it
through the custom template engine) because the source-text checks are
exactly what the audit (``audit_parallelization_dormant``) enforces on
the bundled prompt — a drift in the rendered prompt always means a drift
in the source text.
"""

from __future__ import annotations

from pathlib import Path

_PLANNING_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates" / "planning.jinja"
)
_PLANNING_ANALYSIS_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "ralph"
    / "prompts"
    / "templates"
    / "planning_analysis.jinja"
)


def _read_planning_template() -> str:
    return _PLANNING_TEMPLATE.read_text(encoding="utf-8")


def test_planning_prompt_contains_new_heading_and_lacks_old() -> None:
    source = _read_planning_template()
    assert "## Agent-Driven Parallel Execution" in source, (
        "planning prompt must include the new agent-driven section"
    )
    assert "## Same-Workspace Parallel Worker Rules" not in source, (
        "planning prompt must NOT include the legacy same-workspace section"
    )


def test_planning_prompt_new_section_warns_about_fan_out() -> None:
    source = _read_planning_template()
    assert "Ralph-managed fan-out is dormant" in source
    assert "sub-agents" in source
    assert "ralph coordinate" not in source, (
        "planning prompt must not reference the nonexistent ralph coordinate "
        "command, even as a prohibition"
    )


def test_planning_prompts_use_author_facing_markdown_labels() -> None:
    source = _read_planning_template()
    analysis_source = _PLANNING_ANALYSIS_TEMPLATE.read_text(encoding="utf-8")
    combined = source + analysis_source

    for label in ("## Critical Files", "## Parallel Plan", "## Work Units", "Directories:"):
        assert label in combined
    for internal_name in (
        "critical_files",
        "summary.coverage_areas",
        "summary.intent",
        "work_units",
        "parallel_plan",
        "allowed_directories",
        "edit_area",
        "expected_evidence",
        "verify_command",
        "plan_items_proven",
        "unit_id",
        "Pydantic model is the source of truth",
        "JSON Schema for the plan artifact",
    ):
        assert internal_name not in combined
    assert ".agent" in source
    assert ".git" in source
