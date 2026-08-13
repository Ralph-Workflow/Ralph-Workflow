"""Rendered-template regression checks for concise planning guidance."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext


def _source(name: str) -> str:
    return TemplateContext.default().registry.get_template(name.removesuffix(".jinja"))


def test_planning_prompt_keeps_optional_parallelism_concise() -> None:
    source = _source("planning.jinja")
    assert "Use subagents only when independent repository discovery" in source
    assert "compact linear plan is valid" in source
    assert "Same-Workspace Parallel Worker Rules" not in source
    assert "ralph coordinate" not in source


def test_planning_prompt_keeps_readable_optional_structure() -> None:
    source = _source("shared/_planning_submission_mechanics.j2")
    assert "### [S-n] Title" in source
    assert "stable and never renumbered" in source
    assert "Project-specific `Type:` values are accepted" not in source
    assert "preserved verbatim" in source


def test_analysis_reviews_substance_not_parallel_document_shape() -> None:
    source = _source("planning_analysis.jinja")
    assert "criterion-level verdicts, not a holistic quality score" in source
    assert "fresh evidence" in source
    assert "nine-dimension" not in source
