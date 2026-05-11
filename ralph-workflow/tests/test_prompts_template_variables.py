from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.protocol.session import AgentSession
from ralph.prompts import template_variables
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.template_variables import capability_template_variables


def test_template_variables_apply_to_template() -> None:
    caps, flags = template_variables.default_caps_and_flags_for_drain(SessionDrain.DEVELOPMENT)
    vars_map = template_variables.capability_template_variables(caps, flags)

    template = (
        "Workspace write allowed: {HAS_WORKSPACE_WRITE}; "
        "Shell allowed: {POLICY_ALLOW_SHELL}; Tools: {MCP_TOOLS_LIST}"
    )
    rendered = template.format_map(vars_map)

    assert "Workspace write allowed: true" in rendered
    assert "Shell allowed: true" in rendered
    assert "write_file" in rendered
    assert "exec" in rendered
    summary = vars_map.get("CAPABILITY_SUMMARY", "")
    assert "Capabilities:" in summary
    assert "Policy Flags:" in summary
    assert "workspace.read" in summary
    assert "allow_shell" in summary


def test_template_variables_from_session_respects_session_data() -> None:
    session = AgentSession(
        session_id="session-1",
        run_id="run-a",
        drain="development",
        capabilities={"workspace.read", "workspace.write_tracked", "git.status_read"},
        policy_flags={"allow_shell"},
    )

    vars_map = template_variables.capability_template_variables_from_session(session)

    assert vars_map["HAS_WORKSPACE_WRITE"] == "true"
    assert vars_map["POLICY_ALLOW_SHELL"] == "true"
    assert "write_file" in vars_map["MCP_TOOLS_LIST"]
    assert "exec" not in vars_map["MCP_TOOLS_LIST"]


def test_specialized_analysis_drain_grants_read_and_exec_defaults() -> None:
    caps, flags = template_variables.default_caps_and_flags_for_drain(
        SessionDrain.DEVELOPMENT_ANALYSIS
    )
    vars_map = template_variables.capability_template_variables(caps, flags)

    # Analysis must NOT grant tracked-write capability.
    assert vars_map["HAS_WORKSPACE_WRITE"] == ""
    # Analysis MUST grant bounded process exec so agents can run verification commands.
    assert vars_map["HAS_PROCESS_EXEC"] == "true"
    # Analysis must remain in a no_edit policy mode.
    assert vars_map["POLICY_NO_EDIT"] == "true"
    # Analysis is NOT a development phase, so allow_shell policy stays off.
    assert vars_map["POLICY_ALLOW_SHELL"] == ""
    # The MCP tool list MUST advertise the read/git/exec/artifact tooling so the
    # rendered prompt tells the agent it can call these tools.
    tool_list = vars_map["MCP_TOOLS_LIST"]
    for required in (
        "read_file",
        "list_directory",
        "list_directory_recursive",
        "directory_tree",
        "search_files",
        "git_diff",
        "git_status",
        "git_log",
        "git_show",
        "exec",
        "ralph_submit_artifact",
        "declare_complete",
    ):
        assert required in tool_list, f"missing {required} in {tool_list}"


def test_review_analysis_drain_grants_read_and_exec_defaults() -> None:
    caps, flags = template_variables.default_caps_and_flags_for_drain(SessionDrain.REVIEW_ANALYSIS)
    vars_map = template_variables.capability_template_variables(caps, flags)

    # Analysis must NOT grant tracked-write capability.
    assert vars_map["HAS_WORKSPACE_WRITE"] == ""
    # Analysis MUST grant bounded process exec so agents can run verification commands.
    assert vars_map["HAS_PROCESS_EXEC"] == "true"
    # Analysis must remain in a no_edit policy mode.
    assert vars_map["POLICY_NO_EDIT"] == "true"
    # Analysis is NOT a development phase, so allow_shell policy stays off.
    assert vars_map["POLICY_ALLOW_SHELL"] == ""
    tool_list = vars_map["MCP_TOOLS_LIST"]
    for required in (
        "read_file",
        "list_directory",
        "list_directory_recursive",
        "directory_tree",
        "search_files",
        "git_diff",
        "git_status",
        "git_log",
        "git_show",
        "exec",
        "ralph_submit_artifact",
        "declare_complete",
    ):
        assert required in tool_list, f"missing {required} in {tool_list}"


@pytest.mark.parametrize(
    "drain,template_name",
    [
        (SessionDrain.DEVELOPMENT_ANALYSIS, "development_analysis.jinja"),
        (SessionDrain.REVIEW_ANALYSIS, "review_analysis.jinja"),
    ],
)
def test_analysis_drain_rendered_prompt_contains_exec_and_read_tooling(
    drain: SessionDrain,
    template_name: str,
) -> None:
    ctx = TemplateContext.default()
    tmpl = ctx.registry.get_template(template_name)
    caps, flags = template_variables.default_caps_and_flags_for_drain(drain)
    base_vars: dict[str, str] = {
        "PROMPT_PATH": "PROMPT.md",
        "PLAN_PATH": ".agent/artifacts/plan.json",
        "LATEST_ARTIFACT": "",
        "LATEST_ARTIFACT_PATH": "",
        "LAST_RETRY_ERROR": "",
    }
    vars_map = {**base_vars, **capability_template_variables(caps, flags)}
    rendered = render_template(tmpl, vars_map, ctx.partials)

    # The EXECUTION block from _mcp_tools.jinja must appear in the rendered output.
    assert "exec" in rendered

    # The MCP TOOLS section must list the full read/git/exec/artifact tooling.
    for tool in (
        "read_file",
        "list_directory",
        "list_directory_recursive",
        "directory_tree",
        "search_files",
        "git_diff",
        "git_status",
        "git_log",
        "git_show",
        "exec",
        "ralph_submit_artifact",
        "declare_complete",
    ):
        assert tool in rendered, f"missing {tool} in rendered {template_name}"

    # The SESSION CAPABILITIES block must list the key capabilities.
    for cap in ("process.exec_bounded", "workspace.read", "git.diff_read", "artifact.submit"):
        assert cap in rendered, f"missing capability {cap} in rendered {template_name}"
