"""Static coherence checks for planning prompt templates."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext


def _source(name: str) -> str:
    return TemplateContext.default().registry.get_template(name.removesuffix(".jinja"))


def test_planning_templates_use_standard_markdown_artifact_tools() -> None:
    combined = "\n".join(
        _source(name)
        for name in (
            "planning.jinja",
            "planning_fallback.jinja",
            "planning_edit.jinja",
            "planning_edit_fallback.jinja",
            "planning_analysis.jinja",
        )
    )

    for retired in ("ralph_submit_plan_section", "ralph_finalize_plan", "ralph_edit_md_plan_step"):
        assert retired not in combined


def test_planning_partials_are_shared_by_every_authoring_variant() -> None:
    for name in ("planning.jinja", "planning_fallback.jinja", "planning_edit.jinja", "planning_edit_fallback.jinja"):
        source = _source(name)
        assert "shared/_planning_thinking.j2" in source
        assert "shared/_planning_submission_mechanics.j2" in source


def test_analysis_prompt_requires_step_or_plan_level_findings() -> None:
    source = _source("planning_analysis.jinja")

    assert "Step: [S-n]" in source
    assert "canonical step ID" in source
