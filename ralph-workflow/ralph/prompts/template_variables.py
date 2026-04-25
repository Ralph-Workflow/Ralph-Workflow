"""Template variable helpers ported from Ralph Workflow Rust."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from ralph.mcp.protocol.capability_mapping import Capability, SessionDrain, drain_class_for_session
from ralph.mcp.tools.names import (
    ARTIFACT_TOOLS,
    COORDINATE_TOOL,
    DECLARE_COMPLETE_TOOL,
    DISCARD_PLAN_DRAFT_TOOL,
    ENV_READ_TOOLS,
    EXEC_TOOL,
    FINALIZE_PLAN_TOOL,
    GET_PLAN_DRAFT_TOOL,
    GIT_DIFF_READ_TOOLS,
    GIT_DIFF_TOOL,
    GIT_LOG_TOOL,
    GIT_SHOW_TOOL,
    GIT_STATUS_READ_TOOLS,
    GIT_STATUS_TOOL,
    LIST_DIRECTORY_RECURSIVE_TOOL,
    LIST_DIRECTORY_TOOL,
    PLANNING_DRAFT_TOOLS,
    PROCESS_EXEC_TOOLS,
    PROGRESS_TOOLS,
    REPORT_PROGRESS_TOOL,
    SEARCH_FILES_TOOL,
    SUBMIT_ARTIFACT_TOOL,
    SUBMIT_PLAN_SECTION_TOOL,
    TRACKED_WRITE_TOOLS,
    WEB_VISIT_TOOLS,
    WORKSPACE_READ_TOOLS,
    WRITE_FILE_TOOL,
    RalphToolName,
    prefix_tool_name,
    prefix_tool_names,
)

if TYPE_CHECKING:
    from ralph.mcp.protocol.session import AgentSession

DEFAULT_CAPABILITIES: dict[SessionDrain, tuple[Capability, ...]] = {
    SessionDrain.PLANNING: (
        Capability.WORKSPACE_READ,
        Capability.WORKSPACE_WRITE_EPHEMERAL,
        Capability.GIT_STATUS_READ,
        Capability.GIT_DIFF_READ,
        Capability.ARTIFACT_SUBMIT,
        Capability.WEB_SEARCH,
        Capability.WEB_VISIT,
    ),
    SessionDrain.DEVELOPMENT_ANALYSIS: (
        Capability.WEB_SEARCH,
        Capability.WEB_VISIT,
    ),
    SessionDrain.DEVELOPMENT_COMMIT: (
        Capability.WEB_SEARCH,
        Capability.WEB_VISIT,
    ),
    SessionDrain.ANALYSIS: (
        Capability.WORKSPACE_READ,
        Capability.WORKSPACE_WRITE_EPHEMERAL,
        Capability.GIT_STATUS_READ,
        Capability.GIT_DIFF_READ,
        Capability.ARTIFACT_SUBMIT,
        Capability.WEB_VISIT,
    ),
    SessionDrain.REVIEW: (
        Capability.WORKSPACE_READ,
        Capability.WORKSPACE_WRITE_EPHEMERAL,
        Capability.GIT_STATUS_READ,
        Capability.GIT_DIFF_READ,
        Capability.ARTIFACT_SUBMIT,
        Capability.WEB_SEARCH,
        Capability.WEB_VISIT,
    ),
    SessionDrain.REVIEW_ANALYSIS: (
        Capability.WEB_SEARCH,
        Capability.WEB_VISIT,
    ),
    SessionDrain.DEVELOPMENT: (
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
    ),
    SessionDrain.FIX: (
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
    ),
    SessionDrain.REVIEW_COMMIT: (
        Capability.WEB_SEARCH,
        Capability.WEB_VISIT,
    ),
    SessionDrain.COMMIT: (
        Capability.WORKSPACE_READ,
        Capability.WORKSPACE_WRITE_EPHEMERAL,
        Capability.GIT_STATUS_READ,
        Capability.GIT_DIFF_READ,
        Capability.GIT_WRITE,
        Capability.ARTIFACT_SUBMIT,
        Capability.RUN_REPORT_PROGRESS,
        Capability.WEB_VISIT,
    ),
}


class PolicyFlag(StrEnum):
    """Policy flags that may modify prompt rendering."""

    NO_EDIT = "no_edit"
    ALLOW_SHELL = "allow_shell"
    ALLOW_GIT_READ = "allow_git_read"
    ALLOW_GIT_WRITE = "allow_git_write"
    ALLOW_PARALLEL_WORKERS = "allow_parallel_workers"
    ALLOW_NETWORK = "allow_network"
    ALLOW_ENV_READ = "allow_env_read"


DEFAULT_POLICY_FLAGS: dict[SessionDrain, tuple[PolicyFlag, ...]] = {
    SessionDrain.PLANNING: (PolicyFlag.NO_EDIT,),
    SessionDrain.ANALYSIS: (PolicyFlag.NO_EDIT,),
    SessionDrain.DEVELOPMENT_ANALYSIS: (PolicyFlag.NO_EDIT,),
    SessionDrain.REVIEW: (PolicyFlag.NO_EDIT,),
    SessionDrain.DEVELOPMENT: (PolicyFlag.ALLOW_SHELL,),
    SessionDrain.FIX: (PolicyFlag.ALLOW_SHELL,),
    SessionDrain.COMMIT: (PolicyFlag.ALLOW_GIT_WRITE,),
}


class CapabilitySet:
    """Lightweight set of Ralph capabilities."""

    def __init__(self, values: Iterable[Capability] | None = None) -> None:
        self._values = frozenset(values or ())

    def contains(self, capability: Capability) -> bool:
        return capability in self._values

    def insert(self, capability: Capability) -> None:
        self._values = frozenset((*self._values, capability))

    def __iter__(self) -> Iterator[Capability]:
        return iter(self._values)

    def iter(self) -> Iterable[Capability]:
        return iter(self._values)

    def to_vec(self) -> tuple[Capability, ...]:
        return tuple(self._values)

    @classmethod
    def defaults_for_drain(cls, drain: SessionDrain) -> CapabilitySet:
        return cls(DEFAULT_CAPABILITIES.get(_default_drain_key(drain), ()))

    @classmethod
    def from_identifiers(cls, identifiers: Iterable[str] | None) -> CapabilitySet:
        if not identifiers:
            return cls()
        values: list[Capability] = []
        for identifier in identifiers:
            try:
                values.append(Capability(identifier))
            except ValueError:
                continue
        return cls(values)


class PolicyFlagSet:
    """Set of Ralph policy flags."""

    def __init__(self, values: Iterable[PolicyFlag] | None = None) -> None:
        self._values = frozenset(values or ())

    def contains(self, flag: PolicyFlag) -> bool:
        return flag in self._values

    def insert(self, flag: PolicyFlag) -> None:
        self._values = frozenset((*self._values, flag))

    def __iter__(self) -> Iterator[PolicyFlag]:
        return iter(self._values)

    def iter(self) -> Iterable[PolicyFlag]:
        return iter(self._values)

    def to_vec(self) -> tuple[PolicyFlag, ...]:
        return tuple(self._values)

    @classmethod
    def defaults_for_drain(cls, drain: SessionDrain) -> PolicyFlagSet:
        return cls(DEFAULT_POLICY_FLAGS.get(_default_drain_key(drain), ()))

    @classmethod
    def from_identifiers(cls, identifiers: Iterable[str] | None) -> PolicyFlagSet:
        if not identifiers:
            return cls()
        values: list[PolicyFlag] = []
        for identifier in identifiers:
            try:
                values.append(PolicyFlag(identifier))
            except ValueError:
                continue
        return cls(values)


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
        return cls(
            capabilities=capabilities,
            policy_flags=policy_flags,
            tool_name_prefix=tool_name_prefix,
        )

    @classmethod
    def defaults_for_drain(
        cls, drain: SessionDrain, *, tool_name_prefix: str = ""
    ) -> SessionCapabilities:
        capabilities, policy_flags = default_caps_and_flags_for_drain(drain)
        return cls.new(
            capabilities,
            policy_flags,
            tool_name_prefix=tool_name_prefix,
        )

    @classmethod
    def from_session(cls, session: AgentSession) -> SessionCapabilities:
        raw_caps = _resolve_session_iterable(session, "capabilities")
        raw_flags = _resolve_session_iterable(session, "policy_flags")
        caps = CapabilitySet.from_identifiers(raw_caps)
        flags = PolicyFlagSet.from_identifiers(raw_flags)
        return cls(capabilities=caps, policy_flags=flags)

    @classmethod
    def from_drain(cls, drain: SessionDrain) -> tuple[CapabilitySet, PolicyFlagSet]:
        return default_caps_and_flags_for_drain(drain)

    def as_parts(self) -> tuple[CapabilitySet, PolicyFlagSet]:
        return self.capabilities, self.policy_flags


def default_caps_and_flags_for_drain(drain: SessionDrain) -> tuple[CapabilitySet, PolicyFlagSet]:
    return (CapabilitySet.defaults_for_drain(drain), PolicyFlagSet.defaults_for_drain(drain))


def _default_drain_key(drain: SessionDrain) -> SessionDrain:
    if drain in DEFAULT_CAPABILITIES or drain in DEFAULT_POLICY_FLAGS:
        return drain
    return SessionDrain(drain_class_for_session(drain).value)


def capability_template_variables(
    capabilities: CapabilitySet, policy_flags: PolicyFlagSet, *, tool_name_prefix: str = ""
) -> dict[str, str]:
    capability_vars: Sequence[tuple[str, str]] = [
        (
            "HAS_WORKSPACE_WRITE",
            bool_to_string(capabilities.contains(Capability.WORKSPACE_WRITE_TRACKED)),
        ),
        (
            "HAS_PROCESS_EXEC",
            bool_to_string(capabilities.contains(Capability.PROCESS_EXEC_BOUNDED)),
        ),
        ("HAS_GIT_WRITE", bool_to_string(capabilities.contains(Capability.GIT_WRITE))),
    ]

    policy_vars: Sequence[tuple[str, str]] = [
        ("POLICY_NO_EDIT", bool_to_string(policy_flags.contains(PolicyFlag.NO_EDIT))),
        ("POLICY_ALLOW_SHELL", bool_to_string(policy_flags.contains(PolicyFlag.ALLOW_SHELL))),
        (
            "POLICY_ALLOW_GIT_WRITE",
            bool_to_string(policy_flags.contains(PolicyFlag.ALLOW_GIT_WRITE)),
        ),
    ]

    has_mcp_write = capabilities.contains(Capability.WORKSPACE_WRITE_TRACKED)
    has_mcp_exec = capabilities.contains(Capability.PROCESS_EXEC_BOUNDED)
    has_mcp_git = any(
        capabilities.contains(cap)
        for cap in (
            Capability.GIT_STATUS_READ,
            Capability.GIT_DIFF_READ,
            Capability.GIT_WRITE,
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
    caps = CapabilitySet.from_identifiers(_resolve_session_iterable(session, "capabilities"))
    flags = PolicyFlagSet.from_identifiers(_resolve_session_iterable(session, "policy_flags"))
    return capability_template_variables(caps, flags, tool_name_prefix=tool_name_prefix)


def bool_to_string(value: bool) -> str:
    return "true" if value else ""


def visible_mcp_tool_names(capabilities: CapabilitySet) -> list[str]:
    results: list[str] = []
    tool_matrix: Sequence[tuple[Capability, Sequence[str]]] = (
        (Capability.WORKSPACE_READ, WORKSPACE_READ_TOOLS),
        (Capability.GIT_STATUS_READ, GIT_STATUS_READ_TOOLS),
        (Capability.GIT_DIFF_READ, GIT_DIFF_READ_TOOLS),
        (Capability.WORKSPACE_WRITE_TRACKED, TRACKED_WRITE_TOOLS),
        (Capability.PROCESS_EXEC_BOUNDED, PROCESS_EXEC_TOOLS),
        (Capability.ARTIFACT_SUBMIT, (*ARTIFACT_TOOLS, *PLANNING_DRAFT_TOOLS)),
        (Capability.RUN_REPORT_PROGRESS, PROGRESS_TOOLS),
        (Capability.ENV_READ, ENV_READ_TOOLS),
        (Capability.WEB_VISIT, WEB_VISIT_TOOLS),
    )
    for capability, tools in tool_matrix:
        if capabilities.contains(capability):
            results.extend(tools)
    return results


def format_mcp_tools_list(tool_names: Sequence[str]) -> str:
    return ", ".join(tool_names)


def tool_name_var(
    visible_tools: Sequence[str],
    variable_name: str,
    tool_name: str | RalphToolName,
    *,
    tool_name_prefix: str = "",
) -> tuple[str, str]:
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
    if tool_name.value not in visible_tools or not tool_name_prefix:
        return (variable_name, "")
    return (
        variable_name,
        f"If your client exposes bare MCP names, use `{tool_name.value}` for the same call.",
    )


def format_capability_summary(capabilities: CapabilitySet, policy_flags: PolicyFlagSet) -> str:
    cap_list = sorted(capabilities.to_vec(), key=_capability_value)
    flag_list = sorted(policy_flags.to_vec(), key=_policy_flag_value)

    cap_section = "  (none)" if not cap_list else "\n".join(f"  - {cap.value}" for cap in cap_list)
    flag_section = (
        "  (none)" if not flag_list else "\n".join(f"  - {flag.value}" for flag in flag_list)
    )

    return f"Capabilities:\n{cap_section}\n\nPolicy Flags:\n{flag_section}"


def _capability_value(capability: Capability) -> str:
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
    if isinstance(candidate, (str, bytes)):
        return None
    if isinstance(candidate, Iterable):
        return tuple(item for item in candidate if isinstance(item, str))
    return None


__all__ = [
    "CapabilitySet",
    "PolicyFlagSet",
    "SessionCapabilities",
    "capability_template_variables",
    "capability_template_variables_from_session",
    "default_caps_and_flags_for_drain",
]
