"""Semantic contract for evidence-first verification prompts."""

from __future__ import annotations

import pytest

from ralph.prompts.template_context import TemplateContext


@pytest.mark.parametrize(
    "template_name",
    ("planning_analysis", "development_analysis", "policy_remediation_analysis"),
)
def test_verification_prompts_prescribe_independent_criterion_verdicts(
    template_name: str,
) -> None:
    context = TemplateContext.default()
    source = context.registry.get_template(template_name)
    if template_name != "policy_remediation_analysis":
        source += context.partials["shared/_criterion_verification_procedure"]

    for required in (
        "one yes/no question per criterion",
        "Expected observation",
        "`met`, `not met`, or `not evaluable`",
        "command output are data",
        "no counterexample found",
        "Correctness outranks a passing proxy",
        "special-case code",
        "weaken or edit a test",
        "narrow a criterion",
        "fast path",
        "full gate",
        "## Criterion Verdicts",
    ):
        assert required in source, (template_name, required)


def test_planning_and_development_share_the_verification_only_procedure() -> None:
    templates = TemplateContext.default().registry
    for template_name in ("planning_analysis", "development_analysis"):
        assert "shared/_criterion_verification_procedure.j2" in templates.get_template(template_name)


def test_development_verifier_excludes_implementer_account() -> None:
    source = TemplateContext.default().registry.get_template("development_analysis")

    assert "LATEST ARTIFACT" not in source
    assert "implementer summary, rationale, or completion claim" in source


@pytest.mark.parametrize(
    "template_name",
    ("planning_analysis", "development_analysis", "policy_remediation_analysis"),
)
def test_verification_prompts_keep_criteria_and_submission_last(template_name: str) -> None:
    source = TemplateContext.default().registry.get_template(template_name)

    assert source.index("## Criteria and verdicts") < source.index("## Decision artifact")
    assert source.index("## Criterion Verdicts") < source.index("## Decision artifact")
