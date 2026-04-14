from __future__ import annotations

from ralph.mcp.capability_mapping import SessionDrain
from ralph.mcp.session_bridge import AgentSession

from ralph.prompts import template_variables


def test_template_variables_apply_to_template() -> None:
    caps, flags = template_variables.default_caps_and_flags_for_drain(
        SessionDrain.DEVELOPMENT
    )
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
