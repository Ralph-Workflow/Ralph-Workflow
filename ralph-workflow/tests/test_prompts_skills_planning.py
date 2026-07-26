"""Planning prompt and skill guidance remains concise and standard-path only."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext
from ralph.skills import get_skill_content


def _source(name: str) -> str:
    return TemplateContext.default().registry.get_template(name.removesuffix(".jinja"))


def test_planning_templates_do_not_repeat_the_format_contract() -> None:
    for name in ("planning.jinja", "planning_fallback.jinja"):
        source = _source(name)
        assert "PLAN QUALITY RUBRIC" not in source
        assert "Worked plan example" not in source
        assert "shared/_planning_thinking.j2" in source


def test_plan_skill_teaches_standard_document_revision() -> None:
    skill = get_skill_content("submit-plan-artifact")
    assert "ralph_edit_md_artifact" in skill
    assert "ralph_stage_md_artifact" in skill
    assert "ralph_edit_md_plan_step" not in skill


def test_writing_plans_skill_does_not_require_fixed_headers_or_commits() -> None:
    skill = get_skill_content("writing-plans")
    assert "Every plan MUST start with this header" not in skill
    assert "Frequent commits" not in skill
