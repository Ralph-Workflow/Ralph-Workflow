"""Semantic contract for evidence-first verification prompts."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables


def _render_verifier(template_name: str) -> str:
    context = TemplateContext.default()
    session = SessionCapabilities.defaults_for_drain(SessionDrain.ANALYSIS)
    return render_template(
        context.registry.get_template(template_name),
        {
            **capability_template_variables(session.capabilities, session.policy_flags),
            "PRODUCT_CRITERIA_PATH": "fixtures/request.md",
            "HAS_DOCS_MCP": "",
            "DOCS_MCP_PORT": "localhost:6280",
            "DOCS_LOOKUP_PHASE": "analysis",
            "DOCS_LOOKUP_VARIANT": "",
            "PLAN_PATH": "fixtures/plan.md",
            "LAST_RETRY_ERROR": "",
            "gate_script_policy_path": "docs/gates.md",
            "approved_tools": "python",
            "submit_tool_names": "ralph_submit_md_artifact",
            "verify_tool_names": "ralph_verify_md_artifact",
            "declare_complete_tool_names": "declare_complete",
            "artifact_type": "policy_remediation_analysis_decision",
        },
        context.partials,
    )


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
        "do not propose remedies",
    ):
        assert required in source, (template_name, required)


def test_rendered_verifiers_put_the_evidence_first_contract_before_final_submission() -> None:
    for template_name in (
        "planning_analysis",
        "development_analysis",
        "policy_remediation_analysis",
    ):
        rendered = _render_verifier(template_name)
        contract_start = rendered.index("## Criteria and verdicts")
        final_action = rendered.index("## Decision artifact")
        assert contract_start < final_action
        for required in (
            "Expected observation",
            "`met`, `not met`, or `not evaluable`",
            "implementer summary, rationale, or completion claim",
            "no counterexample found",
            "Correctness outranks a passing proxy",
            "do not propose remedies",
        ):
            assert required in rendered[contract_start:final_action], (template_name, required)


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
