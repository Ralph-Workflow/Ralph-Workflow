"""Prompt-facing typed capability helpers.

This module is a thin facade over ``template_variables`` so prompt materialization
and prompt tests share one capability/policy implementation instead of carrying a
second parallel type system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.prompts.template_variables import (
    CapabilitySet,
    PolicyFlag,
    PolicyFlagSet,
    bool_to_string,
    format_capability_summary,
    format_mcp_tools_list,
    visible_mcp_tool_names,
)
from ralph.prompts.template_variables import (
    capability_template_variables as _capability_template_variables,
)

if TYPE_CHECKING:
    from ralph.mcp.capability_mapping import Capability, SessionDrain
    from ralph.mcp.tool_names import (
        ARTIFACT_TOOLS,
        ENV_READ_TOOLS,
        GIT_DIFF_READ_TOOLS,
        GIT_STATUS_READ_TOOLS,
        PLANNING_DRAFT_TOOLS,
        PROCESS_EXEC_TOOLS,
        PROGRESS_TOOLS,
        TRACKED_WRITE_TOOLS,
        WORKSPACE_READ_TOOLS,
    )
else:
    from ralph.mcp.capability_mapping import Capability, SessionDrain
    from ralph.mcp.tool_names import (
        ARTIFACT_TOOLS,
        ENV_READ_TOOLS,
        GIT_DIFF_READ_TOOLS,
        GIT_STATUS_READ_TOOLS,
        PLANNING_DRAFT_TOOLS,
        PROCESS_EXEC_TOOLS,
        PROGRESS_TOOLS,
        TRACKED_WRITE_TOOLS,
        WORKSPACE_READ_TOOLS,
    )


@dataclass(frozen=True)
class SessionCapabilities:
    """Bundle of capability/policy sets plus transport-specific prompt decoration."""

    capabilities: CapabilitySet
    policy_flags: PolicyFlagSet
    tool_name_prefix: str = ""

    @classmethod
    def defaults_for_drain(
        cls, drain: SessionDrain, *, tool_name_prefix: str = ""
    ) -> SessionCapabilities:
        return cls(
            capabilities=CapabilitySet.defaults_for_drain(drain),
            policy_flags=PolicyFlagSet.defaults_for_drain(drain),
            tool_name_prefix=tool_name_prefix,
        )


def capability_template_variables(
    capabilities: CapabilitySet,
    policy_flags: PolicyFlagSet,
    *,
    tool_name_prefix: str = "",
) -> dict[str, str]:
    return _capability_template_variables(
        capabilities,
        policy_flags,
        tool_name_prefix=tool_name_prefix,
    )


def bool_to_template_value(value: bool) -> str:
    return bool_to_string(value)


__all__ = [
    "ARTIFACT_TOOLS",
    "ENV_READ_TOOLS",
    "GIT_DIFF_READ_TOOLS",
    "GIT_STATUS_READ_TOOLS",
    "PLANNING_DRAFT_TOOLS",
    "PROCESS_EXEC_TOOLS",
    "PROGRESS_TOOLS",
    "TRACKED_WRITE_TOOLS",
    "WORKSPACE_READ_TOOLS",
    "Capability",
    "CapabilitySet",
    "PolicyFlag",
    "PolicyFlagSet",
    "SessionCapabilities",
    "SessionDrain",
    "bool_to_template_value",
    "capability_template_variables",
    "format_capability_summary",
    "format_mcp_tools_list",
    "visible_mcp_tool_names",
]
