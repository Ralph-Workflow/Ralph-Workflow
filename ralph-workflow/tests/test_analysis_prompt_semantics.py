"""Semantic contract for evidence-first verification prompts."""

from __future__ import annotations

from functools import cache

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables


@cache
def _render_verifier(template_name: str) -> str:
    # ``functools.cache`` memoizes the deterministic rendered output of
    # the static packaged template set so the three parameterized tests
    # in this module share one Jinja-environment compilation. Without
    # the cache, each parameterization re-pays the full compilation
    # cost and the per-test wall-clock trips the 1 s SIGALRM budget
    # (``ralph/verify_timeout.py:DEFAULT_TEST_TIMEOUT_SECONDS``) when
    # the rest of the suite is running in parallel.
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
        "Report only material, localized findings",
    ):
        assert required in source, (template_name, required)


@pytest.mark.parametrize(
    "template_name",
    ("planning_analysis", "development_analysis", "policy_remediation_analysis"),
)
def test_rendered_verifiers_put_the_evidence_first_contract_before_final_submission(
    template_name: str,
) -> None:
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
        "Report only material, localized findings",
    ):
        assert required in rendered[contract_start:final_action], (template_name, required)


def test_no_remedies_rule_lives_only_in_policy_remediation_analysis() -> None:
    """The 'do not propose remedies' rule is phase-local to policy_remediation_analysis.

    The planning-analysis phase requires the analyzer to surface concrete
    proposed revisions the planner applies or rebuts, so the no-remedies
    rule must not leak into the shared verification procedure or into the
    planning-analysis template. The development-analysis template uses the
    shared procedure, so it inherits whatever the shared partial says and
    must therefore not assert the no-remedies phrase either.
    """
    needle = "do not propose remedies"
    context = TemplateContext.default()
    shared = context.partials["shared/_criterion_verification_procedure"]

    assert needle not in shared

    for template_name in ("planning_analysis", "development_analysis"):
        source = context.registry.get_template(template_name)
        assert needle not in source, template_name

    policy_remediation = context.registry.get_template("policy_remediation_analysis")
    assert needle in policy_remediation.lower()


def test_planning_analysis_prompts_the_revision_loop_contract() -> None:
    """planning_analysis.jinja requires each finding carry a proposed revision.

    PRODUCT_CRITERIA §1 requires feedback to help the planner produce a
    better plan. Every ``## What Came Up Short`` finding must surface a
    concrete ``Proposed revision:`` the planner either applies or rebuts.
    The lock here keeps the contract one place per rule: the prompt is
    authoritative; the format doc references it.
    """
    source = TemplateContext.default().registry.get_template("planning_analysis")
    lowered = source.lower()

    for required in (
        "concrete proposed revision",
        "applies or rebuts",
        "proposed revision:",
        "plain prose plan can pass",
        "serialized without a reason",
    ):
        assert required in lowered, required

    # The example must model the new contract so renderers copy it.
    assert "[PA-001] Plan-level: Criterion: parallel decomposition." in source
    assert "Proposed revision: split the work into a shared-contract unit" in source


def test_planning_edit_requires_apply_or_rebut_per_finding() -> None:
    """planning_edit.jinja makes the revision loop explicit.

    The planner must apply or rebut every finding, not silently drop it.
    Lock both the main and the fallback template so the rule lives in one
    place per template (each has its own reviewer surface).
    """
    context = TemplateContext.default()

    for template_name in ("planning_edit", "planning_edit_fallback"):
        source = context.registry.get_template(template_name)
        assert "apply the proposed revision or rebut it" in source, template_name
        assert "do not silently drop findings" in source, template_name
        assert "Repair every supported finding" in source, template_name


def test_planning_and_development_share_the_verification_only_procedure() -> None:
    templates = TemplateContext.default().registry
    for template_name in ("planning_analysis", "development_analysis"):
        assert "shared/_criterion_verification_procedure.j2" in templates.get_template(
            template_name
        )


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


def test_planning_analysis_includes_five_substantive_criteria() -> None:
    source = " ".join(TemplateContext.default().registry.get_template("planning_analysis").split())

    for criterion in ("coverage", "truthfulness", "actionability", "parallel decomposition", "execution conflicts"):
        assert criterion in source
    assert "Do not grade formatting" in source
    assert "## Criterion Verdicts" in source
    assert "## Decision artifact" in source
    assert "propose a concrete unit split" in source


def test_development_analysis_prescribes_concrete_verification_fanout() -> None:
    """S-11: development_analysis.jinja must describe a concrete fan-out pattern.

    The prompt dispatches read-only verification subagents, one per criterion
    group (build, lint, types, focused tests), each returning the four-field
    shape (Expected / Observed / Evidence / Location) and the reproduce rule:
    every subagent lead a verdict relies on — passing or failing — must be
    reproduced in the main session before the verdict is written.
    """
    context = TemplateContext.default()
    source = context.registry.get_template("development_analysis")
    source += context.partials["shared/_criterion_verification_procedure"]

    # Four-field return format.
    for required in ("Expected", "Observed", "Evidence", "Location"):
        assert required in source, required

    # Concrete criterion groups named in the prompt.
    for group in ("build", "lint", "types", "focused tests"):
        assert group in source, group

    # Reproduce rule: every relied-on lead is re-run in the main session.
    assert "reproduce" in source.lower(), "reproduce rule missing"
    # The S-2 rule is preserved (subagent output is a lead, not evidence by itself).
    assert "lead" in source.lower()

    # Pinned framing must survive.
    for pinned in (
        "Expected observation",
        "`met`, `not met`, or `not evaluable`",
        "no counterexample found",
        "## Criterion Verdicts",
    ):
        assert pinned in source, pinned

    # The new fan-out block lives between the inspection intro and Decision artifact.
    intro = source.index("inspect the current worktree")
    decision = source.index("## Decision artifact")
    fanout = source.lower().index("reproduce")
    assert intro < fanout < decision
