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
        inputs=DeveloperPromptInputs(prompt_content="Implement it", plan_content="### [S-1] Change it"),
        workspace=workspace,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
    )

    assert "IMPLEMENTATION MODE" in prompt
    assert "### [S-1] Change it" in prompt
    assert "development_result" in prompt
    assert "ralph_submit_md_artifact" in prompt


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
    assert "PLAN001" in prompt
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
    assert "fresh reviewer, not a document-shape checklist" in prompt
    assert "ralph_edit_md_plan_step" not in prompt


def test_planning_history_is_referenced_when_available(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    history = str(tmp_path / ".agent" / "artifacts" / "history" / "plan" / "index.md")
    prompt = prompt_planning_xml_with_context(
        context=TemplateContext.default(),
        inputs=PlanningPromptInputs(prompt_content="Plan it", artifact_history_path=history),
        workspace=workspace,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
    )

    assert history in prompt
