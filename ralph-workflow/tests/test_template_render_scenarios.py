from __future__ import annotations

import re

from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import TemplateRenderer
from ralph.prompts.template_registry import packaged_template_root
from ralph.prompts.types import FROZEN_UNIVERSAL_SKILLS, skill_template_variables
from ralph.testing.audit_template_render_integrity import realistic_render_scenarios

_SKILL_VARIABLE_RE = re.compile(r"\bSKILL_INSTALLED_[A-Z0-9_]+\b")


def test_skill_scenarios_cover_installed_and_absent_template_branches() -> None:
    context = TemplateContext.default()
    top_level_sources = {
        path.stem: context.registry.get_template(path.stem)
        for path in packaged_template_root().glob("*.jinja")
    }
    all_sources = {**context.partials, **top_level_sources}
    skill_variables = {
        match.group()
        for source in all_sources.values()
        for match in _SKILL_VARIABLE_RE.finditer(source)
    }

    absent_variables = skill_template_variables(())
    installed_variables = skill_template_variables(FROZEN_UNIVERSAL_SKILLS)
    assert skill_variables == absent_variables.keys() == installed_variables.keys()
    scenarios = realistic_render_scenarios(skill_variables)
    for variable in skill_variables:
        assert {values[variable] for _name, values in scenarios} == {"", "true"}

    template = context.partials["shared/_developer_iteration_guidance"]
    renderer = TemplateRenderer(context.partials)
    absent = renderer.render(template, absent_variables)
    installed = renderer.render(template, installed_variables)

    assert "Write failing tests before writing implementation code" in absent
    assert "Use test-driven-development when implementing behavior changes" in installed
