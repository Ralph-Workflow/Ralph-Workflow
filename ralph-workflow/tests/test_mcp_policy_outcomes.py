from __future__ import annotations

from ralph.mcp.artifacts.policy_outcomes import APPROVED_POLICY_OUTCOMES, is_policy_approved
from ralph.mcp.protocol.capability_mapping import Capability, McpCapability, lookup_ralph_capability
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools.names import RalphToolName, claude_tool_name, claude_tool_name_prefix


def test_is_policy_approved_accepts_true_and_known_strings() -> None:
    assert is_policy_approved(True) is True
    assert is_policy_approved("approved") is True
    assert is_policy_approved(" allow ") is True
    assert is_policy_approved("allowed") is True


def test_is_policy_approved_accepts_dict_and_object_shapes() -> None:
    class StatusOutcome:
        status = "approved"

    assert is_policy_approved({"name": "allow"}) is True
    assert is_policy_approved({"value": "allowed"}) is True
    assert is_policy_approved(StatusOutcome()) is True


def test_is_policy_approved_rejects_non_approved_values() -> None:
    assert is_policy_approved(False) is False
    assert is_policy_approved(None) is False
    assert is_policy_approved("denied") is False
    assert is_policy_approved({"status": "reject"}) is False


def test_approved_policy_outcomes_are_frozen_and_complete() -> None:
    assert frozenset({"approved", "allow", "allowed"}) == APPROVED_POLICY_OUTCOMES


def test_ralph_tool_name_is_string_enum_compatible() -> None:
    assert RalphToolName.SUBMIT_MD_ARTIFACT == "ralph_submit_md_artifact"
    assert (
        claude_tool_name(RalphToolName.SUBMIT_MD_ARTIFACT)
        == "mcp__ralph__ralph_submit_md_artifact"
    )


def test_claude_tool_name_supports_non_ralph_server_names() -> None:
    assert claude_tool_name("sequentialthinking", server_name="sequential-thinking") == (
        "mcp__sequential-thinking__sequentialthinking"
    )
    assert claude_tool_name_prefix(server_name="angular-cli") == "mcp__angular-cli__"


def test_session_without_upstream_capability_cannot_use_proxied_tool() -> None:
    assert McpCapability.UPSTREAM_TOOL_USE == "UpstreamToolUse"
    session = AgentSession(
        session_id="s-no-upstream",
        run_id="r-no-upstream",
        drain="development",
        capabilities={"WorkspaceRead", "ArtifactSubmit"},
    )
    assert session.check_capability(McpCapability.UPSTREAM_TOOL_USE) == "denied"


def test_session_with_upstream_capability_can_use_proxied_tool() -> None:
    assert McpCapability.UPSTREAM_TOOL_USE == "UpstreamToolUse"
    session = AgentSession(
        session_id="s-with-upstream",
        run_id="r-with-upstream",
        drain="development",
        capabilities={"WorkspaceRead", "ArtifactSubmit", "UpstreamToolUse"},
    )
    assert session.check_capability(McpCapability.UPSTREAM_TOOL_USE) == "approved"


def test_upstream_tool_use_maps_to_ralph_capability() -> None:
    assert Capability.UPSTREAM_TOOL_USE == "upstream.tool_use"
    mapped = lookup_ralph_capability(McpCapability.UPSTREAM_TOOL_USE)
    assert mapped == Capability.UPSTREAM_TOOL_USE
