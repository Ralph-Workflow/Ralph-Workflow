from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.capability_mapping import Capability, SessionDrain
from ralph.prompts import template_variables


@dataclass
class _CallableSession:
    capabilities: object
    policy_flags: object


def test_capability_and_policy_sets_ignore_unknown_identifiers() -> None:
    caps = template_variables.CapabilitySet.from_identifiers(
        ["workspace.read", "not-real", "git.write"]
    )
    flags = template_variables.PolicyFlagSet.from_identifiers(
        ["allow_shell", "unknown", "allow_git_write"]
    )

    assert set(caps.to_vec()) == {Capability.WORKSPACE_READ, Capability.GIT_WRITE}
    assert set(flags.to_vec()) == {
        template_variables.PolicyFlag.ALLOW_SHELL,
        template_variables.PolicyFlag.ALLOW_GIT_WRITE,
    }


def test_session_capabilities_helpers_cover_session_and_drain_paths() -> None:
    callable_session = _CallableSession(
        capabilities=lambda: ["workspace.read", "process.exec_bounded"],
        policy_flags=lambda: ["allow_shell"],
    )

    session_caps = template_variables.SessionCapabilities.from_session(callable_session)
    drain_caps, drain_flags = template_variables.SessionCapabilities.from_drain(SessionDrain.COMMIT)

    assert session_caps.capabilities.contains(Capability.WORKSPACE_READ)
    assert session_caps.capabilities.contains(Capability.PROCESS_EXEC_BOUNDED)
    assert session_caps.policy_flags.contains(template_variables.PolicyFlag.ALLOW_SHELL)
    assert template_variables.SessionCapabilities.new(drain_caps, drain_flags).as_parts() == (
        drain_caps,
        drain_flags,
    )


def test_capability_template_variables_helpers_cover_empty_paths() -> None:
    empty_caps = template_variables.CapabilitySet.from_identifiers(None)
    empty_flags = template_variables.PolicyFlagSet.from_identifiers(None)

    vars_map = template_variables.capability_template_variables(empty_caps, empty_flags)

    assert vars_map["MCP_TOOLS_LIST"] == ""
    assert vars_map["HAS_MCP_WRITE"] == ""
    assert vars_map["HAS_MCP_EXEC"] == ""
    assert vars_map["HAS_MCP_GIT"] == ""
    assert vars_map["CAPABILITY_SUMMARY"] == ("Capabilities:\n  (none)\n\nPolicy Flags:\n  (none)")
    assert template_variables.bool_to_string(True) == "true"
    assert template_variables.bool_to_string(False) == ""
    assert template_variables.tool_name_var([], "EXEC_TOOL_NAME", "exec") == (
        "EXEC_TOOL_NAME",
        "",
    )
    assert template_variables.format_mcp_tools_list(["exec", "write_file"]) == "exec, write_file"


def test_capability_template_variables_from_session_ignores_scalar_attributes() -> None:
    scalar_session = _CallableSession(capabilities="workspace.read", policy_flags=b"allow_shell")

    vars_map = template_variables.capability_template_variables_from_session(scalar_session)

    assert vars_map["MCP_TOOLS_LIST"] == ""
