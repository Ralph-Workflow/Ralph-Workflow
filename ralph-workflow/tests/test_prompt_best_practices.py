"""Regression checks for concise, work-first prompt templates."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_registry import packaged_template_root

_TEMPLATE_NAMES = (
    "planning.jinja",
    "planning_analysis.jinja",
    "worker_developer.jinja",
    "developer_iteration.jinja",
    "development_analysis.jinja",
    "commit_cleanup.jinja",
    "commit_message.jinja",
    "commit_simplified.jinja",
    "conflict_resolution.jinja",
    "policy_remediation.jinja",
    "policy_remediation_analysis.jinja",
    "planning_fallback.jinja",
    "planning_edit.jinja",
    "planning_edit_fallback.jinja",
    "developer_iteration_fallback.jinja",
    "developer_iteration_continuation.jinja",
)


def _templates() -> dict[str, str]:
    context = TemplateContext.default()
    return {
        name: context.registry.get_template(name) for name in _TEMPLATE_NAMES
    } | context.partials


def _first_text_line(source: str) -> str:
    for line in source.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("{%", "{{", "{#")):
            return stripped
    raise AssertionError("template has no text")


def test_prompt_templates_lead_with_the_work_not_a_mode_label() -> None:
    templates = _templates()
    for name in _TEMPLATE_NAMES:
        first_line = _first_text_line(templates[name]).upper()
        assert not first_line.endswith(" MODE"), name
        assert "YOUR ROLE AND GOAL" not in first_line, name


def test_supported_top_level_templates_ship() -> None:
    shipped = {path.name for path in packaged_template_root().glob("*.jinja")}
    assert shipped == set(_TEMPLATE_NAMES)


def test_verification_guidance_is_single_sourced() -> None:
    templates = _templates()
    guidance = templates["shared/_developer_iteration_guidance"]
    commitments = templates["shared/_verification_commitments"]
    assert "Discover the project's narrowest relevant check and full gate" in commitments
    assert "fast path" not in guidance.lower()
    assert "full gate" not in guidance.lower()


def test_developer_iteration_guidance_defaults_to_completion() -> None:
    """All four dev-iteration templates share this partial, so this single
    check covers developer_iteration.jinja, developer_iteration_continuation.jinja,
    developer_iteration_fallback.jinja, and worker_developer.jinja."""
    templates = _templates()
    guidance = templates["shared/_developer_iteration_guidance"]

    assert guidance.index("Completion is the default outcome") < guidance.index("Plan fidelity")
    assert "`status:\ncompleted`" in guidance
    assert "last resort" in guidance
    assert "genuinely exhausted" in guidance
    assert "never\nbecause progress feels slow" in guidance or "never because progress feels slow" in guidance
