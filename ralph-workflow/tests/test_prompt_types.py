"""Tests for prompt capability and policy typing helpers."""

from __future__ import annotations

from ralph.mcp.tools.names import PLANNING_DRAFT_TOOLS
from ralph.prompts.types import (
    ARTIFACT_TOOLS,
    ENV_READ_TOOLS,
    GIT_DIFF_READ_TOOLS,
    GIT_STATUS_READ_TOOLS,
    PROCESS_EXEC_TOOLS,
    PROGRESS_TOOLS,
    TRACKED_WRITE_TOOLS,
    WORKSPACE_READ_TOOLS,
    Capability,
    CapabilitySet,
    PolicyFlag,
    PolicyFlagSet,
    SessionCapabilities,
    SessionDrain,
    bool_to_template_value,
    capability_template_variables,
    format_capability_summary,
    format_mcp_tools_list,
    visible_mcp_tool_names,
)


def test_capability_set_defaults_cover_each_drain() -> None:
    planning = CapabilitySet.defaults_for_drain(SessionDrain.PLANNING)
    development = CapabilitySet.defaults_for_drain(SessionDrain.DEVELOPMENT)
    fix = CapabilitySet.defaults_for_drain(SessionDrain.FIX)
    commit = CapabilitySet.defaults_for_drain(SessionDrain.COMMIT)

    assert set(planning) == {
        Capability.WORKSPACE_READ,
        Capability.WORKSPACE_WRITE_EPHEMERAL,
        Capability.GIT_STATUS_READ,
        Capability.GIT_DIFF_READ,
        Capability.ARTIFACT_SUBMIT,
        Capability.WEB_SEARCH,
        Capability.WEB_VISIT,
    }
    assert set(development) == {
        Capability.WORKSPACE_READ,
        Capability.WORKSPACE_WRITE_EPHEMERAL,
        Capability.WORKSPACE_WRITE_TRACKED,
        Capability.GIT_STATUS_READ,
        Capability.GIT_DIFF_READ,
        Capability.PROCESS_EXEC_BOUNDED,
        Capability.ARTIFACT_SUBMIT,
        Capability.RUN_REPORT_PROGRESS,
        Capability.ENV_READ,
        Capability.WEB_SEARCH,
        Capability.WEB_VISIT,
    }
    assert set(fix) == {
        Capability.WORKSPACE_READ,
        Capability.WORKSPACE_WRITE_TRACKED,
        Capability.GIT_STATUS_READ,
        Capability.GIT_DIFF_READ,
        Capability.PROCESS_EXEC_BOUNDED,
        Capability.ARTIFACT_SUBMIT,
        Capability.RUN_REPORT_PROGRESS,
        Capability.ENV_READ,
        Capability.WEB_SEARCH,
        Capability.WEB_VISIT,
    }
    assert set(commit) == {
        Capability.WORKSPACE_READ,
        Capability.WORKSPACE_WRITE_EPHEMERAL,
        Capability.GIT_STATUS_READ,
        Capability.GIT_DIFF_READ,
        Capability.GIT_WRITE,
        Capability.ARTIFACT_SUBMIT,
        Capability.RUN_REPORT_PROGRESS,
        Capability.WEB_VISIT,
    }


def test_policy_flag_defaults_cover_each_drain() -> None:
    assert set(PolicyFlagSet.defaults_for_drain(SessionDrain.PLANNING)) == {PolicyFlag.NO_EDIT}
    assert set(PolicyFlagSet.defaults_for_drain(SessionDrain.ANALYSIS)) == {PolicyFlag.NO_EDIT}
    assert set(PolicyFlagSet.defaults_for_drain(SessionDrain.REVIEW)) == {PolicyFlag.NO_EDIT}
    assert set(PolicyFlagSet.defaults_for_drain(SessionDrain.DEVELOPMENT)) == {
        PolicyFlag.ALLOW_SHELL
    }
    assert set(PolicyFlagSet.defaults_for_drain(SessionDrain.FIX)) == {PolicyFlag.ALLOW_SHELL}
    assert set(PolicyFlagSet.defaults_for_drain(SessionDrain.COMMIT)) == {
        PolicyFlag.ALLOW_GIT_WRITE
    }


def test_session_capabilities_defaults_bundle_capabilities_and_flags() -> None:
    session = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)

    assert session.capabilities.contains(Capability.WORKSPACE_WRITE_TRACKED)
    assert session.policy_flags.contains(PolicyFlag.ALLOW_SHELL)


def test_visible_mcp_tool_names_respects_enabled_capabilities() -> None:
    capabilities = CapabilitySet(
        [
            Capability.WORKSPACE_READ,
            Capability.GIT_STATUS_READ,
            Capability.GIT_DIFF_READ,
            Capability.WORKSPACE_WRITE_TRACKED,
            Capability.PROCESS_EXEC_BOUNDED,
            Capability.ARTIFACT_SUBMIT,
            Capability.RUN_REPORT_PROGRESS,
            Capability.ENV_READ,
        ]
    )

    assert visible_mcp_tool_names(capabilities) == [
        *WORKSPACE_READ_TOOLS,
        *GIT_STATUS_READ_TOOLS,
        *GIT_DIFF_READ_TOOLS,
        *TRACKED_WRITE_TOOLS,
        *PROCESS_EXEC_TOOLS,
        *ARTIFACT_TOOLS,
        *PLANNING_DRAFT_TOOLS,
        *PROGRESS_TOOLS,
        *ENV_READ_TOOLS,
    ]


def test_capability_template_variables_include_enabled_tools_and_flags() -> None:
    capabilities = CapabilitySet.defaults_for_drain(SessionDrain.DEVELOPMENT)
    policy_flags = PolicyFlagSet.defaults_for_drain(SessionDrain.DEVELOPMENT)

    variables = capability_template_variables(capabilities, policy_flags)

    assert variables["HAS_WORKSPACE_WRITE"] == "true"
    assert variables["HAS_PROCESS_EXEC"] == "true"
    assert variables["HAS_GIT_WRITE"] == ""
    assert variables["POLICY_ALLOW_SHELL"] == "true"
    assert variables["POLICY_ALLOW_GIT_WRITE"] == ""
    assert variables["MCP_TOOLS_LIST"] == format_mcp_tools_list(
        visible_mcp_tool_names(capabilities)
    )
    assert variables["HAS_MCP_WRITE"] == "true"
    assert variables["HAS_MCP_EXEC"] == "true"
    assert variables["HAS_MCP_GIT"] == "true"
    assert variables["SUBMIT_ARTIFACT_TOOL_NAME"] == "ralph_submit_artifact"
    assert variables["SUBMIT_PLAN_SECTION_TOOL_NAME"] == "ralph_submit_plan_section"
    assert variables["FINALIZE_PLAN_TOOL_NAME"] == "ralph_finalize_plan"
    assert variables["GET_PLAN_DRAFT_TOOL_NAME"] == "ralph_get_plan_draft"
    assert variables["DISCARD_PLAN_DRAFT_TOOL_NAME"] == "ralph_discard_plan_draft"
    assert variables["DECLARE_COMPLETE_TOOL_NAME"] == "declare_complete"
    assert variables["COORDINATE_TOOL_NAME"] == "coordinate"
    assert variables["REPORT_PROGRESS_TOOL_NAME"] == "report_progress"
    assert variables["WRITE_FILE_TOOL_NAME"] == "write_file"
    assert variables["LIST_DIRECTORY_TOOL_NAME"] == "list_directory"
    assert variables["LIST_DIRECTORY_RECURSIVE_TOOL_NAME"] == "list_directory_recursive"
    assert variables["SEARCH_FILES_TOOL_NAME"] == "search_files"
    assert variables["EXEC_TOOL_NAME"] == "exec"
    assert variables["GIT_STATUS_TOOL_NAME"] == "git_status"
    assert variables["GIT_DIFF_TOOL_NAME"] == "git_diff"
    assert variables["GIT_LOG_TOOL_NAME"] == "git_log"
    assert variables["GIT_SHOW_TOOL_NAME"] == "git_show"
    assert variables["CAPABILITY_SUMMARY"] == format_capability_summary(capabilities, policy_flags)


def test_capability_template_variables_can_prefix_tool_names_for_claude_mcp() -> None:
    capabilities = CapabilitySet.defaults_for_drain(SessionDrain.DEVELOPMENT)
    policy_flags = PolicyFlagSet.defaults_for_drain(SessionDrain.DEVELOPMENT)

    variables = capability_template_variables(
        capabilities,
        policy_flags,
        tool_name_prefix="mcp__ralph__",
    )

    assert variables["SUBMIT_ARTIFACT_TOOL_NAME"] == "mcp__ralph__ralph_submit_artifact"
    assert variables["SUBMIT_PLAN_SECTION_TOOL_NAME"] == "mcp__ralph__ralph_submit_plan_section"
    assert variables["EXEC_TOOL_NAME"] == "mcp__ralph__exec"
    assert variables["WRITE_FILE_TOOL_REFERENCE"] == "`mcp__ralph__write_file` or bare `write_file`"
    assert variables["EXEC_TOOL_REFERENCE"] == "`mcp__ralph__exec` or bare `exec`"
    assert (
        variables["DECLARE_COMPLETE_TOOL_REFERENCE"]
        == "`mcp__ralph__declare_complete` or bare `declare_complete`"
    )
    assert variables["MCP_TOOLS_LIST"].startswith("mcp__ralph__read_file")


def test_capability_template_variables_leave_disabled_tool_names_empty() -> None:
    capabilities = CapabilitySet([Capability.WORKSPACE_READ])
    policy_flags = PolicyFlagSet([PolicyFlag.NO_EDIT])

    variables = capability_template_variables(capabilities, policy_flags)

    assert variables["MCP_TOOLS_LIST"] == format_mcp_tools_list(list(WORKSPACE_READ_TOOLS))
    assert variables["WRITE_FILE_TOOL_NAME"] == ""
    assert variables["EXEC_TOOL_NAME"] == ""
    assert variables["GIT_DIFF_TOOL_NAME"] == ""
    assert variables["SUBMIT_ARTIFACT_TOOL_NAME"] == ""
    assert variables["HAS_MCP_GIT"] == ""


def test_format_helpers_cover_empty_and_populated_sets() -> None:
    assert format_mcp_tools_list(["read_file", "search_files"]) == "read_file, search_files"
    assert bool_to_template_value(True) == "true"
    assert bool_to_template_value(False) == ""

    empty_summary = format_capability_summary(CapabilitySet(), PolicyFlagSet())
    assert empty_summary == "Capabilities:\n  (none)\n\nPolicy Flags:\n  (none)"

    populated_summary = format_capability_summary(
        CapabilitySet([Capability.GIT_WRITE, Capability.WORKSPACE_READ]),
        PolicyFlagSet([PolicyFlag.ALLOW_GIT_WRITE]),
    )
    assert populated_summary == (
        "Capabilities:\n  - git.write\n  - workspace.read\n\nPolicy Flags:\n  - allow_git_write"
    )
