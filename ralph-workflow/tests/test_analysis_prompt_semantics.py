"""Semantic contract for concise planning analysis."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext


def _source() -> str:
    return TemplateContext.default().registry.get_template("planning_analysis")


def test_planning_analysis_reviews_request_and_repository_substance() -> None:
    source = _source()
    assert source.startswith("You are the planning analysis reviewer.")
    assert "Read the current plan" in source
    assert "and the referenced repository areas" in source
    assert "do not grade document shape" in source


def test_planning_analysis_requires_actionable_costed_findings() -> None:
    source = _source()
    assert "Only report a finding you can prove" in source
    assert "concrete cost" in source
    assert "exact plan-text change that resolves it" in source


def test_planning_analysis_allows_no_findings() -> None:
    source = _source()
    assert "`completed` with no findings is the normal result" in source
    assert "Use `completed` when no proven substantive gap remains" in source


def test_planning_analysis_keeps_matching_remediation_ids() -> None:
    source = _source()
    assert "matching `PA-###`" in source
    assert "`Observation:`, `Cost:`, and `Fix:`" in source
