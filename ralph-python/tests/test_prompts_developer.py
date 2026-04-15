"""Tests for developer and planning prompt helpers."""

from pathlib import Path
from unittest.mock import patch

from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import TemplateRenderingError
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
        inputs=DeveloperPromptInputs(prompt_content=prompt_text, plan_content=plan_text),
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
            prompt_content="Plan the MCP work",
            workspace=workspace,
            session_caps=session_caps,
        )

    assert 'artifact_type="plan"' in prompt
    assert "plan.json" in prompt
    assert "<ralph-plan>" not in prompt


def test_developer_prompt_fallback_uses_json_result_artifact_contract(tmp_path):
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

    assert 'artifact_type="development_result"' in prompt
    assert "development_result.json" in prompt
    assert "<ralph-development-result>" not in prompt


def test_default_artifacts_policy_uses_plan_artifact_type() -> None:
    policy_path = (
        "/Users/mistlight/Projects/RalphWithReviewer/wt-72-ts-conversion/ralph-python/"
        "ralph/policy/defaults/artifacts.toml"
    )
    with Path(policy_path).open(encoding="utf-8") as handle:
        content = handle.read()

    assert 'artifact_type = "plan"' in content
    assert 'artifact_type = "planning_json"' not in content
