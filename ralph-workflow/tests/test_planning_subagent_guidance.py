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


def test_fallback_templates_delegate_discovery_by_default() -> None:
    for name in ("planning_fallback.jinja", "planning_edit_fallback.jinja"):
        source = _source(name)
        assert "shared/_subagents.j2" in source
        assert "Use a subagent only" not in source


def test_analysis_owns_a_concise_substantive_review() -> None:
    source = _source("planning_analysis.jinja")
    assert "## Review contract" in source
    assert "## PLAN QUALITY RUBRIC" not in source
    assert "criterion-level verdicts, not a holistic quality score" in source
