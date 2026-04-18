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
    assert workspace.absolute_path(".agent/CURRENT_PROMPT.md") in prompt
    assert plan_text in prompt
    assert "content_path" in prompt
    assert "edit `.agent/artifacts/development_result.json`" in prompt


def test_developer_iteration_continuation_prompt_includes_exact_resubmit_call(tmp_path):
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
    assert "content_path" in prompt
    assert ".agent/artifacts/development_result.json" in prompt


def test_developer_iteration_continuation_prompt_uses_prefixed_submit_tool_name(tmp_path):
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(
        SessionDrain.DEVELOPMENT,
        tool_name_prefix="mcp__ralph__",
    )

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

    assert "mcp__ralph__ralph_submit_artifact" in prompt
    assert "use `ralph_submit_artifact` for the same call" in prompt.lower()


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
        prompt_content="Plan the unattended pipeline fix",
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
    assert "edit `.agent/artifacts/plan.json`" in prompt
    assert "resubmit with `content_path`" in prompt
    assert "The `content` argument must be a JSON string whose decoded object" in prompt
    assert "Do NOT wrap the payload in outer `type` or `content` fields" in prompt
    assert '"summary": {' in prompt
    assert '"steps": [' in prompt
    assert '"critical_files": {' in prompt
    assert '"risks_mitigations": [' in prompt
    assert '"verification_strategy": [' in prompt
    assert "`summary.scope_items` must contain at least 3 concrete items" in prompt


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
    assert "Unless the plan is genuinely short" in prompt
    assert "ralph_submit_plan_section" in prompt
    assert "ralph_finalize_plan" in prompt
    assert "plan.json" in prompt
    assert "content_path" in prompt
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
            prompt_content="Plan the MCP work",
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
    assert "content_path" in prompt
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
    assert "mcp__ralph__ralph_submit_artifact" in prompt
    assert "or bare `ralph_submit_artifact`" in prompt
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
