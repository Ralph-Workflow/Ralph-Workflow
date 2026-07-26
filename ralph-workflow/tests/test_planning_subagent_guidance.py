"""Regression checks for concise planning guidance."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext


def _source(name: str) -> str:
    return TemplateContext.default().registry.get_template(name.removesuffix(".jinja"))


def test_primary_templates_share_thinking_first_guidance() -> None:
    for name in ("planning.jinja", "planning_edit.jinja"):
        source = _source(name)
        assert "shared/_planning_thinking.j2" in source
        assert "subagent" in source.lower() or name == "planning_edit.jinja"


def test_primary_prompt_keeps_parallelism_optional() -> None:
    source = _source("planning.jinja")
    assert "compact linear plan is valid" in source
    assert "disjoint" in source


def test_fallback_templates_keep_a_condensed_sequential_hint() -> None:
    for name in ("planning_fallback.jinja", "planning_edit_fallback.jinja"):
        source = _source(name)
        assert "subagent" in source.lower() or "sequential" in source.lower()


def test_analysis_owns_a_concise_substantive_review() -> None:
    source = _source("planning_analysis.jinja")
    assert "## Review contract" in source
    assert "## PLAN QUALITY RUBRIC" not in source
    assert "do not grade document shape" in source
