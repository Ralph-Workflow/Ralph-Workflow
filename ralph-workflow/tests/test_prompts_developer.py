"""Tests for developer and planning prompt helpers."""

from pathlib import Path
from unittest.mock import patch

from ralph.prompts.developer import (
    DeveloperPromptInputs,
    PlanningPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import TemplateRenderingError
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

PLANNING_EDIT_GET_DRAFT_TEXT = (
    "Use `ralph_get_plan_draft` to inspect the current finalized plan "
    "or staged draft before editing."
)
PLANNING_EDIT_DEFECT_SCOPE_TEXT = (
    "Before revising any section, classify the feedback scope as one of:"
)
PLANNING_EDIT_GLOBAL_REDERIVATION_TEXT = (
    "If any feedback item reveals repo-wide incompleteness, invalid inventory, incorrect paths, "
    "narrow verification, or prompt-to-plan traceability gaps, you MUST re-derive the plan"
)
PLANNING_EDIT_FINALIZE_TEXT = (
    "Use `ralph_finalize_plan` after revising the affected sections so "
    "the updated plan replaces the prior finalized plan."
)
PLANNING_EDIT_SELF_AUDIT_TEXT = "Before `ralph_finalize_plan`, perform this self-audit:"
PLANNING_EDIT_RISK_COVERAGE_TEXT = (
    "- Risk coverage: concrete risks, mitigations, and edge cases are represented"
)
PLANNING_EDIT_PARALLELIZATION_TEXT = (
    "- Parallelization safety: any parallel work remains disjoint, realistic, "
    "and policy-compliant"
)
PLANNING_EDIT_MAINTAINABILITY_TEXT = (
    "- Maintainability and handoff quality: the plan stays concise, "
    "non-redundant, and explicit for development handoff"
)
PLANNING_EDIT_SCOPE_INVALIDATION_TEXT = (
    "If the ORIGINAL REQUEST has repository-wide acceptance criteria and the current plan "
    "narrowed scope before running repository-wide discovery"
)
PLANNING_EDIT_DISCOVERY_FIRST_TEXT = (
    "replace the summary, scope, and early steps so Step 1 becomes repo-wide discovery"
)
PLANNING_EDIT_SCOPE_DERIVATION_TEXT = (
    "- Scope derivation: when the task is repo-wide, implementation scope comes from an "
    "explicit repo-wide discovery step rather than a guessed subsystem"
)
PLANNING_EDIT_PASS_TARGET_TEXT = (
    "Your target is to submit the strongest revised plan you can so the next planning-analysis pass"
)
PLANNING_EDIT_NO_KNOWN_GAPS_TEXT = (
    "Do not finalize a draft that still has any known unresolved analyzer finding"
)
PLANNING_EDIT_DEPENDENT_SECTION_REWRITE_TEXT = (
    "If fixing one section changes the truth of another section, replace every dependent section"
)
PLANNING_EDIT_NEXT_ANALYZER_TEXT = (
    "Before finalizing, proactively search for any additional repo-grounded failure"
)
PLANNING_EDIT_SURFACED_BLOCKER_TEXT = (
    "If a canonical verification command or repo-wide audit already surfaces a blocker "
    "during replanning"
)
PLANNING_EDIT_RULE_CATEGORY_TEXT = (
    "When the ORIGINAL REQUEST imposes repo-wide structural rules, build a repo-wide inventory"
)
PLANNING_EDIT_NO_EXCEPTION_TEXT = (
    "Do not preserve prompt-violating tests, files, or workflows as justified exceptions"
)
PLANNING_EDIT_STARTING_POINT_TEXT = (
    "Treat the planning-analysis feedback as a starting point, not as the full list of issues"
)
PLANNING_EDIT_NOT_LOCAL_PATCH_TEXT = (
    "Do not localize your revision pass to only the sections explicitly cited by the analyzer"
)
PLANNING_EDIT_SELF_ANALYSIS_TEXT = (
    "You must perform your own repo-grounded analysis before finalizing"
)
PLANNING_EDIT_ISSUE_MAPPING_TEXT = (
    "Every analyzer issue must map to concrete revised sections or an explicit verified reason"
)


def test_developer_iteration_prompt_includes_plan_and_unattended_section(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    prompt_text = "Implement the widget feature"
    plan_text = "1. Update the API\n2. Wire the UI"

    prompt = prompt_developer_iteration_xml_with_context(
        context=context,
        inputs=DeveloperPromptInputs(prompt_content=prompt_text, plan_content=plan_text),
        workspace=workspace,
        session_caps=session_caps,
    )

    assert "IMPLEMENTATION MODE" in prompt
    assert "UNATTENDED MODE" in prompt
    assert workspace.absolute_path(".agent/CURRENT_PROMPT.md") in prompt
    assert plan_text in prompt
    assert "ARTIFACT SUBMISSION" not in prompt
    assert "development_result" not in prompt
    assert "content_path" not in prompt


def test_developer_iteration_continuation_prompt_stays_focused_on_remaining_work(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)

    prompt = prompt_developer_iteration_xml_with_context(
        context=context,
        inputs=DeveloperPromptInputs(
            prompt_content="Continue implementing the widget feature",
            plan_content="1. Finish backend\n2. Finish UI",
        ),
        workspace=workspace,
        session_caps=session_caps,
        template_name="developer_iteration_continuation.jinja",
    )

    assert "continuing a DEVELOPMENT iteration" in prompt
    assert "content_path" not in prompt
    assert "development_result" not in prompt


def test_planning_prompt_uses_defaults_and_mcp_tools(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)

    prompt = prompt_planning_xml_with_context(
        context=context,
        inputs=PlanningPromptInputs(prompt_content=None),
        workspace=workspace,
        session_caps=session_caps,
    )

    assert "PLANNING MODE" in prompt
    assert workspace.absolute_path(".agent/CURRENT_PROMPT.md") in prompt
    assert "ralph_submit_artifact" in prompt
    assert "ralph_submit_plan_section" in prompt
    assert "ralph_finalize_plan" in prompt


def test_planning_prompt_describes_detailed_raw_plan_payload_contract(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)

    prompt = prompt_planning_xml_with_context(
        context=context,
        inputs=PlanningPromptInputs(prompt_content="Plan the unattended pipeline fix"),
        workspace=workspace,
        session_caps=session_caps,
    )

    assert 'artifact_type="plan"' in prompt
    assert "Unless the plan is genuinely short" in prompt
    assert "submit each required section separately" in prompt
    assert "Use `ralph_submit_plan_section`" in prompt
    assert "Use `ralph_get_plan_draft`" in prompt
    assert "Use `ralph_discard_plan_draft`" in prompt
    assert "Use `ralph_finalize_plan`" in prompt
    assert "edit `.agent/artifacts/plan.json`" not in prompt
    assert "resubmit with `content_path`" not in prompt
    assert "The `content` argument must be a JSON string whose decoded object" in prompt
    assert "Do NOT wrap the payload in outer `type` or `content` fields" in prompt
    assert '"summary": {' in prompt
    assert '"steps": [' in prompt
    assert '"critical_files": {' in prompt
    assert '"risks_mitigations": [' in prompt
    assert '"verification_strategy": [' in prompt
    assert "`summary.scope_items` must contain at least 3 concrete items" in prompt


def test_planning_edit_prompt_teaches_mcp_plan_revision_flow(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)

    prompt = prompt_planning_xml_with_context(
        context=context,
        inputs=PlanningPromptInputs(
            prompt_content="Revise the unattended pipeline fix plan",
            analysis_feedback_content="The previous plan needs narrower verification.",
            analysis_feedback_path=workspace.absolute_path(".agent/PLANNING_ANALYSIS_DECISION.md"),
        ),
        workspace=workspace,
        session_caps=session_caps,
        template_name="planning_edit.jinja",
    )

    assert "PLANNING EDIT MODE" in prompt
    assert "The prior plan was rejected by planning analysis." in prompt
    assert PLANNING_EDIT_GET_DRAFT_TEXT in prompt
    assert PLANNING_EDIT_DEFECT_SCOPE_TEXT in prompt
    assert PLANNING_EDIT_GLOBAL_REDERIVATION_TEXT in prompt
    assert PLANNING_EDIT_FINALIZE_TEXT in prompt
    assert PLANNING_EDIT_SELF_AUDIT_TEXT in prompt
    assert PLANNING_EDIT_RISK_COVERAGE_TEXT in prompt
    assert PLANNING_EDIT_PARALLELIZATION_TEXT in prompt
    assert PLANNING_EDIT_MAINTAINABILITY_TEXT in prompt
    assert PLANNING_EDIT_SCOPE_INVALIDATION_TEXT in prompt
    assert PLANNING_EDIT_DISCOVERY_FIRST_TEXT in prompt
    assert PLANNING_EDIT_SCOPE_DERIVATION_TEXT in prompt
    assert PLANNING_EDIT_PASS_TARGET_TEXT in prompt
    assert PLANNING_EDIT_NO_KNOWN_GAPS_TEXT in prompt
    assert PLANNING_EDIT_DEPENDENT_SECTION_REWRITE_TEXT in prompt
    assert PLANNING_EDIT_NEXT_ANALYZER_TEXT in prompt
    assert PLANNING_EDIT_SURFACED_BLOCKER_TEXT in prompt
    assert PLANNING_EDIT_RULE_CATEGORY_TEXT in prompt
    assert PLANNING_EDIT_NO_EXCEPTION_TEXT in prompt
    assert PLANNING_EDIT_STARTING_POINT_TEXT in prompt
    assert PLANNING_EDIT_NOT_LOCAL_PATCH_TEXT in prompt
    assert PLANNING_EDIT_SELF_ANALYSIS_TEXT in prompt
    assert PLANNING_EDIT_ISSUE_MAPPING_TEXT in prompt
    assert "Use `ralph_discard_plan_draft` only when the existing plan is unsalvageable" in prompt
    assert "artifact_type=\"plan\"" not in prompt
    assert workspace.absolute_path(".agent/PLANNING_ANALYSIS_DECISION.md") in prompt


def test_planning_prompt_fallback_uses_json_plan_artifact_contract(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)

    with patch(
        "ralph.prompts.developer.render_template",
        side_effect=TemplateRenderingError("boom"),
    ):
        prompt = prompt_planning_xml_with_context(
            context=context,
            inputs=PlanningPromptInputs(prompt_content="Plan the MCP work"),
            workspace=workspace,
            session_caps=session_caps,
        )

    assert 'artifact_type="plan"' in prompt
    assert "Unless the plan is genuinely short" in prompt
    assert "ralph_submit_plan_section" in prompt
    assert "ralph_finalize_plan" in prompt
    assert "plan.json" not in prompt
    assert "content_path" not in prompt
    assert "<ralph-plan>" not in prompt


def test_planning_prompt_fallback_uses_prefixed_tool_names(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(
        SessionDrain.PLANNING,
        tool_name_prefix="mcp__ralph__",
    )

    with patch(
        "ralph.prompts.developer.render_template",
        side_effect=TemplateRenderingError("boom"),
    ):
        prompt = prompt_planning_xml_with_context(
            context=context,
            inputs=PlanningPromptInputs(prompt_content="Plan the MCP work"),
            workspace=workspace,
            session_caps=session_caps,
        )

    assert "mcp__ralph__ralph_submit_plan_section" in prompt
    assert "mcp__ralph__ralph_finalize_plan" in prompt
    assert "mcp__ralph__ralph_get_plan_draft" in prompt
    assert "mcp__ralph__ralph_discard_plan_draft" in prompt
    assert "mcp__ralph__ralph_submit_artifact" in prompt
    assert "or bare `ralph_submit_plan_section`" in prompt
    assert "or bare `ralph_finalize_plan`" in prompt
    assert "or bare `ralph_get_plan_draft`" in prompt
    assert "or bare `ralph_discard_plan_draft`" in prompt
    assert "or bare `ralph_submit_artifact`" in prompt
    assert workspace.absolute_path(".agent/CURRENT_PROMPT.md") in prompt
    assert "{{" not in prompt
    assert "{%" not in prompt


def test_developer_prompt_fallback_omits_result_artifact_contract(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)

    with patch(
        "ralph.prompts.developer.render_template",
        side_effect=TemplateRenderingError("boom"),
    ):
        prompt = prompt_developer_iteration_xml_with_context(
            context=context,
            inputs=DeveloperPromptInputs(
                prompt_content="Implement MCP hardening",
                plan_content="1. Add tests\n2. Fix capability checks",
            ),
            workspace=workspace,
            session_caps=session_caps,
        )

    assert "development_result" not in prompt
    assert "content_path" not in prompt
    assert "ralph_submit_artifact" not in prompt
    assert "<ralph-development-result>" not in prompt


def test_developer_prompt_fallback_uses_prefixed_tool_names_and_exec_guidance(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(
        SessionDrain.DEVELOPMENT,
        tool_name_prefix="mcp__ralph__",
    )

    with patch(
        "ralph.prompts.developer.render_template",
        side_effect=TemplateRenderingError("boom"),
    ):
        prompt = prompt_developer_iteration_xml_with_context(
            context=context,
            inputs=DeveloperPromptInputs(
                prompt_content="Implement MCP hardening",
                plan_content="1. Add tests\n2. Fix capability checks",
            ),
            workspace=workspace,
            session_caps=session_caps,
        )

    assert "Native agent tools are disabled" in prompt
    assert "mcp__ralph__exec" in prompt
    assert "mcp__ralph__report_progress" in prompt
    assert "mcp__ralph__ralph_submit_artifact" not in prompt
    assert "or bare `ralph_submit_artifact`" not in prompt
    assert workspace.absolute_path(".agent/CURRENT_PROMPT.md") in prompt
    assert str(tmp_path / ".agent" / "tmp" / "prompt_payloads" / "development_plan.txt") in prompt
    assert "{{" not in prompt
    assert "{%" not in prompt


def test_default_artifacts_policy_uses_plan_artifact_type() -> None:
    policy_path = Path(__file__).parents[1] / "ralph" / "policy" / "defaults" / "artifacts.toml"
    with policy_path.open(encoding="utf-8") as handle:
        content = handle.read()

    assert 'artifact_type = "plan"' in content
    assert 'artifact_type = "planning_json"' not in content


def test_planning_prompt_with_artifact_history_path_shows_history_section(tmp_path: Path) -> None:
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)
    history_path = str(tmp_path / ".agent" / "artifacts" / "history" / "plan" / "index.md")

    prompt = prompt_planning_xml_with_context(
        context=context,
        inputs=PlanningPromptInputs(
            prompt_content="Plan the feature",
            artifact_history_path=history_path,
        ),
        workspace=workspace,
        session_caps=session_caps,
    )

    assert "ARTIFACT HISTORY" in prompt
    assert history_path in prompt


def test_planning_prompt_without_history_path_omits_history_section(tmp_path: Path) -> None:
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)

    prompt = prompt_planning_xml_with_context(
        context=context,
        inputs=PlanningPromptInputs(prompt_content="Plan the feature"),
        workspace=workspace,
        session_caps=session_caps,
    )

    assert "ARTIFACT HISTORY" not in prompt
