"""Planning prompt and skill guidance remains concise and current."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext
from ralph.skills import get_skill_content


def test_planning_templates_delegate_to_shared_contract() -> None:
    context = TemplateContext.default()
    for name in ("planning.jinja", "planning_fallback.jinja"):
        source = context.registry.get_template(name.removesuffix(".jinja"))
        assert "PLAN QUALITY RUBRIC" not in source
        assert "shared/_planning_thinking.j2" in source
        assert "shared/_planning_submission_mechanics.j2" in source


def test_plan_skill_teaches_current_artifact_flow() -> None:
    skill = get_skill_content("submit-plan-artifact")

    assert "ralph_edit_md_artifact" in skill
    assert "schema_version" in skill
    assert "Validation Overrides" in skill
    assert "ralph_edit_md_plan_step" not in skill


def test_writing_plans_skill_requires_stable_steps_without_fixed_sections() -> None:
    skill = get_skill_content("writing-plans")

    assert "stable `### [S-n] Title` steps" in skill
    assert "Orient" in skill
