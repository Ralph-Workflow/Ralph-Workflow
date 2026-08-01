"""Regression checks for concise, work-first prompt templates."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext

_TEMPLATE_NAMES = (
    "planning.jinja",
    "planning_analysis.jinja",
    "worker_developer.jinja",
    "developer_iteration.jinja",
    "review.jinja",
    "development_analysis.jinja",
    "review_analysis.jinja",
    "fix_mode.jinja",
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


def test_review_templates_share_one_evidence_decision_contract() -> None:
    templates = _templates()
    for name in ("development_analysis.jinja", "review_analysis.jinja", "review.jinja"):
        assert "shared/_analysis_decision_contract.j2" in templates[name]


def test_evidence_decision_contract_owns_shared_review_dimensions() -> None:
    templates = _templates()
    contract = templates["shared/_analysis_decision_contract"]
    assert (
        "plan compliance, security, correctness, performance, maintainability, and testing"
        in contract
    )


def test_verification_guidance_is_single_sourced() -> None:
    templates = _templates()
    guidance = templates["shared/_developer_iteration_guidance"]
    commitments = templates["shared/_verification_commitments"]
    assert "Discover the project's gate" in commitments
    assert "fast path" not in guidance.lower()
    assert "full gate" not in guidance.lower()
