"""Template variable helpers ported from Ralph Workflow Rust."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.mcp.protocol.capability_mapping import Capability as RalphCapability
from ralph.mcp.tool_contract import visible_tool_names_for_capabilities
from ralph.mcp.tools.names import (
    COORDINATE_TOOL,
    DECLARE_COMPLETE_TOOL,
    DISCARD_PLAN_DRAFT_TOOL,
    EXEC_TOOL,
    FINALIZE_PLAN_TOOL,
    GET_PLAN_DRAFT_TOOL,
    GIT_DIFF_TOOL,
    GIT_LOG_TOOL,
    GIT_SHOW_TOOL,
    GIT_STATUS_TOOL,
    INSERT_PLAN_STEP_TOOL,
    LIST_DIRECTORY_RECURSIVE_TOOL,
    LIST_DIRECTORY_TOOL,
    MOVE_PLAN_STEP_TOOL,
    PATCH_PLAN_STEP_TOOL,
    REMOVE_PLAN_STEP_TOOL,
    REPLACE_PLAN_STEP_TOOL,
    REPORT_PROGRESS_TOOL,
    SEARCH_FILES_TOOL,
    SUBMIT_ARTIFACT_TOOL,
    SUBMIT_PLAN_SECTION_TOOL,
    SUBMIT_PLAN_SECTIONS_TOOL,
    VALIDATE_PLAN_DRAFT_TOOL,
    WRITE_FILE_TOOL,
    RalphToolName,
    prefix_tool_name,
    prefix_tool_names,
)
from ralph.prompts._capability_set import DEFAULT_CAPABILITIES, CapabilitySet
from ralph.prompts._policy_flag import PolicyFlag
from ralph.prompts._policy_flag_set import PolicyFlagSet

if TYPE_CHECKING:
    from ralph.mcp.protocol.capability_mapping import SessionDrain
    from ralph.mcp.protocol.session import AgentSession


def default_capability_identifiers_for_drain(drain: SessionDrain) -> set[str]:
    """Return the canonical default capability identifiers for a drain."""
    return {cap.value for cap in DEFAULT_CAPABILITIES.get(drain, ())}


@dataclass(frozen=True)
class SessionCapabilities:
    """Helper bundling capabilities and policy flags for prompt rendering."""

    capabilities: CapabilitySet
    policy_flags: PolicyFlagSet
    tool_name_prefix: str = ""

    @classmethod
    def new(
        cls,
        capabilities: CapabilitySet,
        policy_flags: PolicyFlagSet,
        *,
        tool_name_prefix: str = "",
    ) -> SessionCapabilities:
        """Build a SessionCapabilities from explicit capability and policy-flag sets."""
        return cls(
            capabilities=capabilities,
            policy_flags=policy_flags,
            tool_name_prefix=tool_name_prefix,
        )

    @classmethod
    def defaults_for_drain(
        cls, drain: SessionDrain, *, tool_name_prefix: str = ""
    ) -> SessionCapabilities:
        """Build a SessionCapabilities using the bundled defaults for the given drain."""
        capabilities, policy_flags = default_caps_and_flags_for_drain(drain)
        return cls.new(
            capabilities,
            policy_flags,
            tool_name_prefix=tool_name_prefix,
        )

    @classmethod
    def from_session(cls, session: AgentSession) -> SessionCapabilities:
        """Build a SessionCapabilities from the live session's identifiers."""
        raw_caps = _resolve_session_iterable(session, "capabilities")
        raw_flags = _resolve_session_iterable(session, "policy_flags")
        caps = CapabilitySet.from_identifiers(raw_caps)
        flags = PolicyFlagSet.from_identifiers(raw_flags)
        return cls(capabilities=caps, policy_flags=flags)

    @classmethod
    def from_drain(cls, drain: SessionDrain) -> tuple[CapabilitySet, PolicyFlagSet]:
        """Return the bundled default (CapabilitySet, PolicyFlagSet) pair for the given drain."""
        return default_caps_and_flags_for_drain(drain)

    def as_parts(self) -> tuple[CapabilitySet, PolicyFlagSet]:
        """Return the (capabilities, policy_flags) tuple this bundle holds."""
        return self.capabilities, self.policy_flags


def default_caps_and_flags_for_drain(drain: SessionDrain) -> tuple[CapabilitySet, PolicyFlagSet]:
    """Return the bundled default (CapabilitySet, PolicyFlagSet) pair for the given drain."""
    return (CapabilitySet.defaults_for_drain(drain), PolicyFlagSet.defaults_for_drain(drain))


def capability_template_variables(
    capabilities: CapabilitySet, policy_flags: PolicyFlagSet, *, tool_name_prefix: str = ""
) -> dict[str, str]:
    """Render prompt template variables for the given capabilities, flags, and tool-name prefix."""
    capability_vars: Sequence[tuple[str, str]] = [
        (
            "HAS_WORKSPACE_WRITE",
            bool_to_string(capabilities.contains(RalphCapability.WORKSPACE_WRITE_TRACKED)),
        ),
        (
            "HAS_PROCESS_EXEC",
            bool_to_string(capabilities.contains(RalphCapability.PROCESS_EXEC_BOUNDED)),
        ),
        ("HAS_GIT_WRITE", bool_to_string(capabilities.contains(RalphCapability.GIT_WRITE))),
    ]

    policy_vars: Sequence[tuple[str, str]] = [
        ("POLICY_NO_EDIT", bool_to_string(policy_flags.contains(PolicyFlag.NO_EDIT))),
        ("POLICY_ALLOW_SHELL", bool_to_string(policy_flags.contains(PolicyFlag.ALLOW_SHELL))),
        (
            "POLICY_ALLOW_GIT_WRITE",
            bool_to_string(policy_flags.contains(PolicyFlag.ALLOW_GIT_WRITE)),
        ),
    ]

    has_mcp_write = capabilities.contains(RalphCapability.WORKSPACE_WRITE_TRACKED)
    has_mcp_exec = capabilities.contains(RalphCapability.PROCESS_EXEC_BOUNDED)
    has_mcp_git = any(
        capabilities.contains(cap)
        for cap in (
            RalphCapability.GIT_STATUS_READ,
            RalphCapability.GIT_DIFF_READ,
            RalphCapability.GIT_WRITE,
        )
    )

    visible_tools = visible_mcp_tool_names(capabilities)
    visible_prompt_tool_names = prefix_tool_names(visible_tools, tool_name_prefix=tool_name_prefix)
    mcp_vars: Sequence[tuple[str, str]] = [
        ("MCP_TOOLS_LIST", format_mcp_tools_list(visible_prompt_tool_names)),
        ("HAS_MCP_WRITE", bool_to_string(has_mcp_write)),
        ("HAS_MCP_EXEC", bool_to_string(has_mcp_exec)),
        ("HAS_MCP_GIT", bool_to_string(has_mcp_git)),
    ]

    mcp_tool_name_vars: Sequence[tuple[str, str]] = [
        tool_name_var(
            visible_tools,
            "SUBMIT_ARTIFACT_TOOL_NAME",
            SUBMIT_ARTIFACT_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "SUBMIT_PLAN_SECTION_TOOL_NAME",
            SUBMIT_PLAN_SECTION_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "FINALIZE_PLAN_TOOL_NAME",
            FINALIZE_PLAN_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "GET_PLAN_DRAFT_TOOL_NAME",
            GET_PLAN_DRAFT_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "DISCARD_PLAN_DRAFT_TOOL_NAME",
            DISCARD_PLAN_DRAFT_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "INSERT_PLAN_STEP_TOOL_NAME",
            INSERT_PLAN_STEP_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "REPLACE_PLAN_STEP_TOOL_NAME",
            REPLACE_PLAN_STEP_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "REMOVE_PLAN_STEP_TOOL_NAME",
            REMOVE_PLAN_STEP_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "MOVE_PLAN_STEP_TOOL_NAME",
            MOVE_PLAN_STEP_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "PATCH_PLAN_STEP_TOOL_NAME",
            PATCH_PLAN_STEP_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "SUBMIT_PLAN_SECTIONS_TOOL_NAME",
            SUBMIT_PLAN_SECTIONS_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "VALIDATE_PLAN_DRAFT_TOOL_NAME",
            VALIDATE_PLAN_DRAFT_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "DECLARE_COMPLETE_TOOL_NAME",
            DECLARE_COMPLETE_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "COORDINATE_TOOL_NAME",
            COORDINATE_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "REPORT_PROGRESS_TOOL_NAME",
            REPORT_PROGRESS_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "WRITE_FILE_TOOL_NAME",
            WRITE_FILE_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "LIST_DIRECTORY_TOOL_NAME",
            LIST_DIRECTORY_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "LIST_DIRECTORY_RECURSIVE_TOOL_NAME",
            LIST_DIRECTORY_RECURSIVE_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "SEARCH_FILES_TOOL_NAME",
            SEARCH_FILES_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "EXEC_TOOL_NAME",
            EXEC_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "GIT_STATUS_TOOL_NAME",
            GIT_STATUS_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "GIT_DIFF_TOOL_NAME",
            GIT_DIFF_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "GIT_LOG_TOOL_NAME",
            GIT_LOG_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_var(
            visible_tools,
            "GIT_SHOW_TOOL_NAME",
            GIT_SHOW_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "SUBMIT_ARTIFACT_TOOL_REFERENCE",
            SUBMIT_ARTIFACT_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "SUBMIT_PLAN_SECTION_TOOL_REFERENCE",
            SUBMIT_PLAN_SECTION_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "FINALIZE_PLAN_TOOL_REFERENCE",
            FINALIZE_PLAN_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "GET_PLAN_DRAFT_TOOL_REFERENCE",
            GET_PLAN_DRAFT_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "DISCARD_PLAN_DRAFT_TOOL_REFERENCE",
            DISCARD_PLAN_DRAFT_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "INSERT_PLAN_STEP_TOOL_REFERENCE",
            INSERT_PLAN_STEP_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "REPLACE_PLAN_STEP_TOOL_REFERENCE",
            REPLACE_PLAN_STEP_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "REMOVE_PLAN_STEP_TOOL_REFERENCE",
            REMOVE_PLAN_STEP_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "MOVE_PLAN_STEP_TOOL_REFERENCE",
            MOVE_PLAN_STEP_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "PATCH_PLAN_STEP_TOOL_REFERENCE",
            PATCH_PLAN_STEP_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "SUBMIT_PLAN_SECTIONS_TOOL_REFERENCE",
            SUBMIT_PLAN_SECTIONS_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "VALIDATE_PLAN_DRAFT_TOOL_REFERENCE",
            VALIDATE_PLAN_DRAFT_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "DECLARE_COMPLETE_TOOL_REFERENCE",
            DECLARE_COMPLETE_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "COORDINATE_TOOL_REFERENCE",
            COORDINATE_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "REPORT_PROGRESS_TOOL_REFERENCE",
            REPORT_PROGRESS_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "WRITE_FILE_TOOL_REFERENCE",
            WRITE_FILE_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "LIST_DIRECTORY_TOOL_REFERENCE",
            LIST_DIRECTORY_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "LIST_DIRECTORY_RECURSIVE_TOOL_REFERENCE",
            LIST_DIRECTORY_RECURSIVE_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "SEARCH_FILES_TOOL_REFERENCE",
            SEARCH_FILES_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        tool_name_reference_var(
            visible_tools,
            "EXEC_TOOL_REFERENCE",
            EXEC_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
        bare_tool_hint_var(
            visible_tools,
            "SUBMIT_ARTIFACT_BARE_HINT",
            SUBMIT_ARTIFACT_TOOL,
            tool_name_prefix=tool_name_prefix,
        ),
    ]

    summary_var = (
        "CAPABILITY_SUMMARY",
        format_capability_summary(capabilities, policy_flags),
    )

    all_items = (
        *capability_vars,
        *policy_vars,
        *mcp_vars,
        *mcp_tool_name_vars,
        summary_var,
    )
    return dict(all_items)


def capability_template_variables_from_session(
    session: AgentSession, *, tool_name_prefix: str = ""
) -> dict[str, str]:
    """Render capability template variables from the live session's identifiers."""
    caps = CapabilitySet.from_identifiers(_resolve_session_iterable(session, "capabilities"))
    flags = PolicyFlagSet.from_identifiers(_resolve_session_iterable(session, "policy_flags"))
    return capability_template_variables(caps, flags, tool_name_prefix=tool_name_prefix)


def bool_to_string(value: bool) -> str:
    """Render a boolean as the template convention 'true' or the empty string."""
    return "true" if value else ""


def visible_mcp_tool_names(capabilities: CapabilitySet) -> list[str]:
    """Return canonical MCP tool names a session with the given capabilities may call."""
    capability_ids = [capability.value for capability in capabilities.to_vec()]
    drain = "planning" if RalphCapability.ARTIFACT_PLAN_WRITE in capabilities.to_vec() else "prompt"
    return visible_tool_names_for_capabilities(capability_ids, drain=drain)


def format_mcp_tools_list(tool_names: Sequence[str]) -> str:
    """Render a sequence of MCP tool names as a single comma-separated string for prompts."""
    return ", ".join(tool_names)


def tool_name_var(
    visible_tools: Sequence[str],
    variable_name: str,
    tool_name: str | RalphToolName,
    *,
    tool_name_prefix: str = "",
) -> tuple[str, str]:
    """Return (variable_name, prefixed_tool_name) when visible, else (variable_name, '')."""
    canonical_name = tool_name.value if isinstance(tool_name, RalphToolName) else tool_name
    if canonical_name not in visible_tools:
        return (variable_name, "")
    return (variable_name, prefix_tool_name(tool_name, tool_name_prefix=tool_name_prefix))


def tool_name_reference_var(
    visible_tools: Sequence[str],
    variable_name: str,
    tool_name: RalphToolName,
    *,
    tool_name_prefix: str = "",
) -> tuple[str, str]:
    """Return (variable_name, prompt_reference) when visible, else (variable_name, '')."""
    if tool_name.value not in visible_tools:
        return (variable_name, "")
    return (variable_name, tool_name.prompt_reference(tool_name_prefix=tool_name_prefix))


def bare_tool_hint_var(
    visible_tools: Sequence[str],
    variable_name: str,
    tool_name: RalphToolName,
    *,
    tool_name_prefix: str = "",
) -> tuple[str, str]:
    """Return (variable_name, hint) for the bare-name fallback when visible."""
    if tool_name.value not in visible_tools or not tool_name_prefix:
        return (variable_name, "")
    return (
        variable_name,
        f"If your client exposes bare MCP names, use `{tool_name.value}` for the same call.",
    )


def format_capability_summary(capabilities: CapabilitySet, policy_flags: PolicyFlagSet) -> str:
    """Render a multi-line summary of granted capabilities and active policy flags for prompts."""
    cap_list = sorted(capabilities.to_vec(), key=_capability_value)
    flag_list = sorted(policy_flags.to_vec(), key=_policy_flag_value)

    cap_section = "  (none)" if not cap_list else "\n".join(f"  - {cap.value}" for cap in cap_list)
    flag_section = (
        "  (none)" if not flag_list else "\n".join(f"  - {flag.value}" for flag in flag_list)
    )

    return f"Capabilities:\n{cap_section}\n\nPolicy Flags:\n{flag_section}"


def _capability_value(capability: RalphCapability) -> str:
    return capability.value


def _policy_flag_value(flag: PolicyFlag) -> str:
    return flag.value


def _resolve_session_iterable(session: object, attribute: str) -> Sequence[str] | None:
    try:
        attributes = cast("dict[str, object]", vars(session))
        candidate = attributes.get(attribute)
    except TypeError:
        return None
    if candidate is None:
        return None
    if callable(candidate):
        candidate = cast("Callable[[], object]", candidate)()
    if isinstance(candidate, str | bytes):
        return None
    if isinstance(candidate, Iterable):
        return tuple(item for item in candidate if isinstance(item, str))
    return None


__all__ = [
    "CapabilitySet",
    "PolicyFlag",
    "PolicyFlagSet",
    "SessionCapabilities",
    "bool_to_string",
    "capability_template_variables",
    "capability_template_variables_from_session",
    "default_capability_identifiers_for_drain",
    "default_caps_and_flags_for_drain",
    "format_capability_summary",
    "format_mcp_tools_list",
]
