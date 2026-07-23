from __future__ import annotations

from ralph.mcp.tools.names import (
    ARTIFACT_SUBMIT_TOOLS,
    PLANNING_DRAFT_TOOLS,
    RalphToolName,
)
from ralph.prompts.template_variables import capability_template_variables
from ralph.prompts.types import SessionCapabilities, SessionDrain


def test_planning_capabilities_expose_only_markdown_artifact_tools() -> None:
    capabilities = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)
    capability_set = capabilities.capabilities

    expected = {str(tool) for tool in (*ARTIFACT_SUBMIT_TOOLS, *PLANNING_DRAFT_TOOLS)}
    from ralph.prompts.template_variables import visible_mcp_tool_names

    visible = set(visible_mcp_tool_names(capability_set))
    assert expected <= visible
    assert all("plan_section" not in tool for tool in visible)
    assert all("submit_artifact" not in tool for tool in visible)


def test_template_variables_name_the_native_markdown_surface() -> None:
    capabilities = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)

    variables = capability_template_variables(
        capabilities.capabilities, capabilities.policy_flags
    )

    assert variables["SUBMIT_MD_ARTIFACT_TOOL_NAME"] == "ralph_submit_md_artifact"
    assert variables["VERIFY_MD_ARTIFACT_TOOL_NAME"] == "ralph_verify_md_artifact"
    assert variables["STAGE_MD_ARTIFACT_TOOL_NAME"] == "ralph_stage_md_artifact"
    assert variables["GET_MD_DRAFT_TOOL_NAME"] == "ralph_get_md_draft"
    assert variables["FINALIZE_MD_ARTIFACT_TOOL_NAME"] == "ralph_finalize_md_artifact"
    assert variables["EDIT_MD_PLAN_STEP_TOOL_NAME"] == "ralph_edit_md_plan_step"


def test_prefixed_template_variables_keep_bare_markdown_aliases_visible() -> None:
    capabilities = SessionCapabilities.defaults_for_drain(
        SessionDrain.PLANNING,
        tool_name_prefix="mcp__ralph__",
    )

    variables = capability_template_variables(
        capabilities.capabilities,
        capabilities.policy_flags,
        tool_name_prefix=capabilities.tool_name_prefix,
    )

    reference = variables["SUBMIT_MD_ARTIFACT_TOOL_REFERENCE"]
    assert "mcp__ralph__ralph_submit_md_artifact" in reference
    assert "ralph_submit_md_artifact" in reference
    assert RalphToolName.SUBMIT_MD_ARTIFACT.value in variables["MCP_TOOLS_LIST"]
