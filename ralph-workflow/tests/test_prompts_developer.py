"""Rendered prompt contracts for development and concise planning guidance."""

from pathlib import Path

from ralph.prompts.developer import (
    DeveloperPromptInputs,
    PlanningPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace


def test_developer_prompt_includes_plan_and_submission_contract(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    prompt = prompt_developer_iteration_xml_with_context(
        context=TemplateContext.default(),
        inputs=DeveloperPromptInputs(
            prompt_content="Implement it", plan_content="### [S-1] Change it"
        ),
        workspace=workspace,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
    )

    assert "IMPLEMENTATION MODE" in prompt
    assert "### [S-1] Change it" in prompt
    assert "development_result" in prompt
    assert "ralph_submit_md_artifact" in prompt
    # Parallel-by-default rewrite: the non-worker developer prompt must
    # state that parallel execution of independent ready units is
    # required by default, the literal heading, the sequential-only
    # exception clause, and the large-plan-cue phrasing. These are the
    # shared-wording-contract anchors the rewrite pins.
    assert "Parallel execution of independent ready units is required by default" in prompt
    assert "## PARALLEL EXECUTION (required by default)" in prompt
    assert "Sequential execution requires an explicit plan reason" in prompt
    assert "not a reason to stop, hand back, split the task, or return `partial`" in prompt
    # Reporting anchor (S-1 edit 2): the development result must justify
    # sequential execution by the plan text that forced it.
    assert "which plan text forced it" in prompt
    # Queue-in-waves wording (S-1 edit 4): a full cap queues remaining
    # ready units, never broadens ownership.
    assert "queue the remaining ready units" in prompt
    # The removed "runtime limit" reporting-anchor escape must be gone.
    assert "runtime limit" not in prompt


def test_planning_prompt_uses_concise_artifact_workflow(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    prompt = prompt_planning_xml_with_context(
        context=TemplateContext.default(),
        inputs=PlanningPromptInputs(prompt_content="Plan the change"),
        workspace=workspace,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
    )

    assert "PLANNING MODE" in prompt
    assert "Orient" in prompt
    assert "Characterize" in prompt
    assert "Change" in prompt
    assert "Verify" in prompt
    assert "executor guidance, not a required document shape" in prompt
    assert "parallel" in prompt
    assert 'artifact_type="plan"' in prompt
    assert "ralph_edit_md_artifact" in prompt
    assert "ralph_edit_md_plan_step" not in prompt


def test_planning_edit_treats_analysis_as_advice(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    prompt = prompt_planning_xml_with_context(
        context=TemplateContext.default(),
        inputs=PlanningPromptInputs(
            prompt_content="Revise it", analysis_feedback_content="Use a narrower check."
        ),
        workspace=workspace,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
        template_name="planning_edit.jinja",
    )

    assert "PLANNING EDIT MODE" in prompt
    assert "fresh repository evidence" in prompt
    assert "ralph_edit_md_plan_step" not in prompt


def test_planning_prompt_omits_plan_history(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    prompt = prompt_planning_xml_with_context(
        context=TemplateContext.default(),
        inputs=PlanningPromptInputs(prompt_content="Plan it"),
        workspace=workspace,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
    )

    assert "Prior plan history is available" not in prompt
    assert "ARTIFACT_HISTORY_PATH" not in prompt
