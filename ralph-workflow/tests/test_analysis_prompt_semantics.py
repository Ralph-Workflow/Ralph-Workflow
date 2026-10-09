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
    source = _render_verifier(template_name)

    planning_only = (
        "one yes/no question per criterion",
        "`met`, `not met`, or `not evaluable`",
        "no counterexample found",
        "## Criterion Verdicts",
        "Expected observation",
    )
    # Free-form contract: the development_analysis prompt does NOT
    # prescribe the structured ``## Criterion Verdicts`` shape, the
    # ``Expected observation:`` field, or the ``met``/``not met``/
    # ``not evaluable`` vocabulary. Coverage is judged in plain
    # language against the request and plan, not against a per-item
    # shape. The free-form guidance lives in the inspect / decision
    # body sections of the development prompt and is asserted by
    # ``test_development_analysis_prescribes_concrete_verification_fanout``
    # and the free-form-decision-body wording below.
    if template_name == "development_analysis":
        for forbidden in planning_only:
            assert forbidden not in source, (template_name, forbidden)
        for required in (
            "implementer summary, rationale, or completion claim",
            # Free-form contract replaces the structured
            # ``fast path``/``full gate`` gate-discovery wording
            # with the planner-style whole-change review
            # (parallel pieces fit together, no unrelated scope,
            # repository policy).
            "parallel pieces fit together",
            "no unrelated scope",
            "repository policy",
        ):
            assert required in source, (template_name, required)
    else:
        for required in planning_only:
            assert required in source, (template_name, required)
        for required in (
            "command output are data",
            "Correctness outranks a passing proxy",
            "special-case code",
            "weaken or edit a test",
            "narrow a criterion",
            "fast path",
            "full gate",
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
    # Free-form contract: development_analysis does not use the
    # structured ``## Criteria and verdicts`` section heading. The
    # evidence-first contract lives in the inspect block of the
    # prompt; that block must precede the Decision artifact block in
    # every analysis type. For structured types the criteria and
    # verdicts section is the same anchor.
    if template_name == "development_analysis":
        contract_marker = "inspect the current worktree"
        assert "## Criteria and verdicts" not in rendered
    else:
        contract_marker = "## Criteria and verdicts"
    contract_start = rendered.index(contract_marker)
    final_action = rendered.index("## Decision artifact")
    assert contract_start < final_action
    if template_name == "development_analysis":
        for required in (
            "implementer summary, rationale, or completion claim",
            # The free-form contract replaces the structured
            # ``Do not change the implementation`` line with the
            # plain-language whole-change review (parallel pieces
            # fit together, no unrelated scope, repository policy).
            "parallel pieces fit together",
            "no unrelated scope",
            "repository policy",
        ):
            assert required in rendered[contract_start:final_action], (template_name, required)
    else:
        for required in (
            "implementer summary, rationale, or completion claim",
            "Correctness outranks a passing proxy",
            "Report only material, localized findings",
            "Do not change the implementation",
        ):
            assert required in rendered[contract_start:final_action], (template_name, required)
    if template_name in ("planning_analysis", "policy_remediation_analysis"):
        for required in (
            "Expected observation",
            "`met`, `not met`, or `not evaluable`",
            "no counterexample found",
        ):
            assert required in rendered[contract_start:final_action], (template_name, required)
    if template_name == "planning_analysis":
        assert "do not propose remedies in this decision" not in rendered
        assert "proposed unit split" in rendered[contract_start:final_action]
        assert "Do not propose other remedies" in rendered[contract_start:final_action]
    elif template_name == "policy_remediation_analysis":
        assert "do not propose remedies in this decision" in rendered[contract_start:final_action]
        assert "proposed unit split" not in rendered[contract_start:final_action]
    else:  # development_analysis: free-form contract
        # The structured ``do not propose remedies in this decision``
        # line and the ``proposed unit split`` vocabulary belong to
        # the planning contract. Development is free-form; the
        # parallel-fix-plan guidance replaces the unit-split
        # vocabulary. See development_analysis.jinja "Free-form
        # decision body" and the follow-plan block in
        # shared/_analysis_context.jinja.
        assert "do not propose remedies in this decision" not in rendered
        assert "proposed unit split" not in rendered
        assert (
            "split the remaining work into independent units" in rendered.lower()
            or "split the remaining work" in rendered.lower()
        )


def test_development_verifier_excludes_implementer_account() -> None:
    source = _render_verifier("development_analysis")

    assert "LATEST ARTIFACT" not in source
    assert "implementer summary, rationale, or completion claim" in source


@pytest.mark.parametrize(
    "template_name",
    ("planning_analysis", "development_analysis", "policy_remediation_analysis"),
)
def test_verification_prompts_keep_criteria_and_submission_last(template_name: str) -> None:
    source = _render_verifier(template_name)

    if template_name == "development_analysis":
        # Free-form contract: the development prompt uses
        # ``## Free-form decision body`` in place of the structured
        # ``## Criteria and verdicts`` / ``## Criterion Verdicts`` blocks.
        contract_marker = "## Free-form decision body"
        assert "## Criteria and verdicts" not in source
        assert "## Criterion Verdicts" not in source
    else:
        contract_marker = "## Criteria and verdicts"
        assert source.index("## Criterion Verdicts") < source.index("## Decision artifact")
    assert source.index(contract_marker) < source.index("## Decision artifact")


def test_planning_analysis_includes_five_substantive_criteria() -> None:
    source = " ".join(_render_verifier("planning_analysis").split())

    for criterion in (
        "coverage",
        "truthfulness",
        "actionability",
        "parallel decomposition",
        "execution conflicts",
    ):
        assert criterion in source
    for expected in (
        "every part of the request",
        "Paths, commands, and current-behavior claims",
        "what to change and how to show it works",
        "reproduce every relied-on lead",
    ):
        assert expected in source
    assert "Do not grade formatting" in source
    assert "## Criterion Verdicts" in source
    assert "## Decision artifact" in source
    assert "propose a concrete unit split" in source
    assert "Name the independent branches" in source
    assert "return `request_changes`" in source
    assert "read-only subagents" in source


def test_development_analysis_prescribes_concrete_verification_fanout() -> None:
    """S-11: development_analysis.jinja must describe a concrete fan-out pattern.

    The prompt dispatches read-only verification subagents, one per criterion
    group (build, lint, types, focused tests), each returning the four-field
    shape (Expected / Observed / Evidence / Location) and the reproduce rule:
    every subagent lead a verdict relies on — passing or failing — must be
    reproduced in the main session before the verdict is written.
    """
    source = _render_verifier("development_analysis")

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

    # Free-form contract: development_analysis does NOT prescribe the
    # structured ``## Criterion Verdicts`` / ``Expected observation:``
    # / ``met``/``not met``/``not evaluable`` vocabulary. The body is
    # free-form; the next agent reads the verdict in plain language.
    for forbidden in (
        "Expected observation",
        "`met`, `not met`, or `not evaluable`",
        "no counterexample found",
        "## Criterion Verdicts",
    ):
        assert forbidden not in source, forbidden

    # The new fan-out block lives between the inspection intro and Decision artifact.
    intro = source.index("inspect the current worktree")
    decision = source.index("## Decision artifact")
    fanout = source.lower().index("reproduce", intro)
    assert intro < fanout < decision
