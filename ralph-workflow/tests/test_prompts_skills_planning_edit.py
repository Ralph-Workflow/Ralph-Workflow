"""Planning-edit prompts reuse the concise shared planning standard."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext


def _source(name: str) -> str:
    return TemplateContext.default().registry.get_template(name.removesuffix(".jinja"))


def test_planning_edit_variants_reuse_shared_thinking_guidance() -> None:
    for name in ("planning_edit.jinja", "planning_edit_fallback.jinja"):
        source = _source(name)
        assert "shared/_planning_thinking.j2" in source
        assert "ralph_edit_md_plan_step" not in source


def test_planning_edit_treats_analysis_as_evidence_not_a_checklist() -> None:
    source = _source("planning_edit.jinja")
    assert "fresh repository\nevidence" in source
    assert "not a document-shape checklist" in source
