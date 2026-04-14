"""Tests for developer and planning prompt helpers."""

from ralph.prompts.developer import (
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace


def test_developer_iteration_prompt_includes_plan_and_unattended_section(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    prompt_text = "Implement the widget feature"
    plan_text = "1. Update the API\n2. Wire the UI"

    prompt = prompt_developer_iteration_xml_with_context(
        context=context,
        prompt_content=prompt_text,
        plan_content=plan_text,
        workspace=workspace,
        session_caps=session_caps,
    )

    assert "IMPLEMENTATION MODE" in prompt
    assert "UNATTENDED MODE" in prompt
    assert prompt_text in prompt
    assert plan_text in prompt


def test_planning_prompt_uses_defaults_and_mcp_tools(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)

    prompt = prompt_planning_xml_with_context(
        context=context,
        prompt_content=None,
        workspace=workspace,
        session_caps=session_caps,
    )

    assert "PLANNING MODE" in prompt
    assert "No requirements provided" in prompt
    assert "ralph_submit_artifact" in prompt
