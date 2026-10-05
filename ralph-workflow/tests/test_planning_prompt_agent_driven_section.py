"""Regression checks for planning's parallel-default guidance."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext


def _source(name: str) -> str:
    return TemplateContext.default().registry.get_template(name.removesuffix(".jinja"))


def test_planning_prompt_delegates_read_only_discovery() -> None:
    source = _source("planning.jinja")
    assert "Delegate exploration" in source
    assert "subagents" in source
    assert "ralph coordinate" not in source


def test_submission_shape_is_optional() -> None:
    source = _source("shared/_planning_submission_mechanics.j2")
    assert "executor-ready Markdown plan" in source
    assert "actual prose authoritative" in source
    assert "at\nleast ten words" not in source


def test_analysis_reviews_substance() -> None:
    source = _source("planning_analysis.jinja")
    assert "coverage, truthfulness, actionability, parallel" in source
    assert "Do not grade formatting" in source
