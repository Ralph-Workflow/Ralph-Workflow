"""Typed capability helpers and template variables for RFC-009 prompts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

WORKSPACE_READ_TOOLS = (
    "read_file",
    "list_directory",
    "list_directory_recursive",
    "search_files",
)
GIT_STATUS_READ_TOOLS = ("git_status", "git_log", "git_show")
GIT_DIFF_READ_TOOLS = ("git_diff",)
TRACKED_WRITE_TOOLS = ("write_file",)
PROCESS_EXEC_TOOLS = ("exec",)
ARTIFACT_TOOLS = ("ralph_submit_artifact", "declare_complete", "coordinate")
PROGRESS_TOOLS = ("report_progress",)
ENV_READ_TOOLS = ("read_env",)


class SessionDrain(str, Enum):
    """Pipeline drain identity for prompt generation."""

    PLANNING = "planning"
    DEVELOPMENT = "development"
    ANALYSIS = "analysis"
    REVIEW = "review"
    FIX = "fix"
    COMMIT = "commit"


class Capability(str, Enum):
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE_EPHEMERAL = "workspace.write_ephemeral"
    WORKSPACE_WRITE_TRACKED = "workspace.write_tracked"
    PROCESS_EXEC_BOUNDED = "process.exec_bounded"
    PROCESS_EXEC_UNBOUNDED = "process.exec_unbounded"
    ARTIFACT_SUBMIT = "artifact.submit"
    RUN_REPORT_PROGRESS = "run.report_progress"
    GIT_STATUS_READ = "git.status_read"
    GIT_DIFF_READ = "git.diff_read"
    GIT_WRITE = "git.write"
    ENV_READ = "env.read"
    ENV_WRITE = "env.write"


class CapabilitySet:
    """Set of capabilities granted to a session."""

    def __init__(self, capabilities: Iterable[Capability] | None = None) -> None:
        self._capabilities: set[Capability] = set(capabilities or [])

    def insert(self, capability: Capability) -> None:
        self._capabilities.add(capability)

    def contains(self, capability: Capability) -> bool:
        return capability in self._capabilities

    def __iter__(self) -> Iterator[Capability]:
        return iter(self._capabilities)

    @classmethod
    def defaults_for_drain(cls, drain: SessionDrain) -> CapabilitySet:
        if drain in {SessionDrain.PLANNING, SessionDrain.ANALYSIS, SessionDrain.REVIEW}:
            caps = [
                Capability.WORKSPACE_READ,
                Capability.WORKSPACE_WRITE_EPHEMERAL,
                Capability.GIT_STATUS_READ,
                Capability.GIT_DIFF_READ,
                Capability.ARTIFACT_SUBMIT,
            ]
        elif drain == SessionDrain.DEVELOPMENT:
            caps = [
                Capability.WORKSPACE_READ,
                Capability.WORKSPACE_WRITE_EPHEMERAL,
                Capability.WORKSPACE_WRITE_TRACKED,
                Capability.GIT_STATUS_READ,
                Capability.GIT_DIFF_READ,
                Capability.PROCESS_EXEC_BOUNDED,
                Capability.ARTIFACT_SUBMIT,
                Capability.RUN_REPORT_PROGRESS,
                Capability.ENV_READ,
            ]
        elif drain == SessionDrain.FIX:
            caps = [
                Capability.WORKSPACE_READ,
                Capability.WORKSPACE_WRITE_TRACKED,
                Capability.GIT_STATUS_READ,
                Capability.GIT_DIFF_READ,
                Capability.PROCESS_EXEC_BOUNDED,
                Capability.ARTIFACT_SUBMIT,
                Capability.RUN_REPORT_PROGRESS,
                Capability.ENV_READ,
            ]
        else:  # commit
            caps = [
                Capability.WORKSPACE_READ,
                Capability.WORKSPACE_WRITE_EPHEMERAL,
                Capability.GIT_STATUS_READ,
                Capability.GIT_DIFF_READ,
                Capability.GIT_WRITE,
                Capability.ARTIFACT_SUBMIT,
                Capability.RUN_REPORT_PROGRESS,
            ]
        return cls(caps)


class PolicyFlag(str, Enum):
    NO_EDIT = "no_edit"
    ALLOW_SHELL = "allow_shell"
    ALLOW_GIT_WRITE = "allow_git_write"
    ALLOW_GIT_READ = "allow_git_read"
    ALLOW_PARALLEL_WORKERS = "allow_parallel_workers"
    ALLOW_NETWORK = "allow_network"
    ALLOW_ENV_READ = "allow_env_read"


class PolicyFlagSet:
    """Set of policy flags for a session."""

    def __init__(self, flags: Iterable[PolicyFlag] | None = None) -> None:
        self._flags: set[PolicyFlag] = set(flags or [])

    def insert(self, flag: PolicyFlag) -> None:
        self._flags.add(flag)

    def contains(self, flag: PolicyFlag) -> bool:
        return flag in self._flags

    def __iter__(self) -> Iterator[PolicyFlag]:
        return iter(self._flags)

    @classmethod
    def defaults_for_drain(cls, drain: SessionDrain) -> PolicyFlagSet:
        if drain in {SessionDrain.PLANNING, SessionDrain.ANALYSIS, SessionDrain.REVIEW}:
            flags = [PolicyFlag.NO_EDIT]
        elif drain in {SessionDrain.DEVELOPMENT, SessionDrain.FIX}:
            flags = [PolicyFlag.ALLOW_SHELL]
        else:
            flags = [PolicyFlag.ALLOW_GIT_WRITE]
        return cls(flags)


@dataclass(frozen=True)
class SessionCapabilities:
    """Bundle of capability and policy flag sets for prompt rendering."""

    capabilities: CapabilitySet
    policy_flags: PolicyFlagSet

    @classmethod
    def defaults_for_drain(cls, drain: SessionDrain) -> SessionCapabilities:
        return cls(
            capabilities=CapabilitySet.defaults_for_drain(drain),
            policy_flags=PolicyFlagSet.defaults_for_drain(drain),
        )


def visible_mcp_tool_names(capabilities: CapabilitySet) -> list[str]:
    """Return the visible MCP tool names for the granted capability set."""

    manifest: list[tuple[bool, tuple[str, ...]]] = [
        (capabilities.contains(Capability.WORKSPACE_READ), WORKSPACE_READ_TOOLS),
        (capabilities.contains(Capability.GIT_STATUS_READ), GIT_STATUS_READ_TOOLS),
        (capabilities.contains(Capability.GIT_DIFF_READ), GIT_DIFF_READ_TOOLS),
        (capabilities.contains(Capability.WORKSPACE_WRITE_TRACKED), TRACKED_WRITE_TOOLS),
        (capabilities.contains(Capability.PROCESS_EXEC_BOUNDED), PROCESS_EXEC_TOOLS),
        (capabilities.contains(Capability.ARTIFACT_SUBMIT), ARTIFACT_TOOLS),
        (capabilities.contains(Capability.RUN_REPORT_PROGRESS), PROGRESS_TOOLS),
        (capabilities.contains(Capability.ENV_READ), ENV_READ_TOOLS),
    ]
    result: list[str] = []
    for enabled, tools in manifest:
        if enabled:
            result.extend(tools)
    return result


def capability_template_variables(
    capabilities: CapabilitySet, policy_flags: PolicyFlagSet
) -> dict[str, str]:
    """Generate template variables from session capabilities and policy flags."""

    vars: dict[str, str] = {
        "HAS_WORKSPACE_WRITE": bool_to_template_value(
            capabilities.contains(Capability.WORKSPACE_WRITE_TRACKED)
        ),
        "HAS_PROCESS_EXEC": bool_to_template_value(
            capabilities.contains(Capability.PROCESS_EXEC_BOUNDED)
        ),
        "HAS_GIT_WRITE": bool_to_template_value(
            capabilities.contains(Capability.GIT_WRITE)
        ),
        "POLICY_NO_EDIT": bool_to_template_value(policy_flags.contains(PolicyFlag.NO_EDIT)),
        "POLICY_ALLOW_SHELL": bool_to_template_value(
            policy_flags.contains(PolicyFlag.ALLOW_SHELL)
        ),
        "POLICY_ALLOW_GIT_WRITE": bool_to_template_value(
            policy_flags.contains(PolicyFlag.ALLOW_GIT_WRITE)
        ),
    }

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
    tool_names = visible_mcp_tool_names(capabilities)

    vars.update(
        {
            "MCP_TOOLS_LIST": format_mcp_tools_list(tool_names),
            "HAS_MCP_WRITE": bool_to_template_value(has_mcp_write),
            "HAS_MCP_EXEC": bool_to_template_value(has_mcp_exec),
            "HAS_MCP_GIT": bool_to_template_value(has_mcp_git),
        }
    )

    tool_mapping: list[tuple[str, str]] = [
        ("SUBMIT_ARTIFACT_TOOL_NAME", "ralph_submit_artifact"),
        ("DECLARE_COMPLETE_TOOL_NAME", "declare_complete"),
        ("COORDINATE_TOOL_NAME", "coordinate"),
        ("REPORT_PROGRESS_TOOL_NAME", "report_progress"),
        ("WRITE_FILE_TOOL_NAME", "write_file"),
        ("LIST_DIRECTORY_TOOL_NAME", "list_directory"),
        ("LIST_DIRECTORY_RECURSIVE_TOOL_NAME", "list_directory_recursive"),
        ("SEARCH_FILES_TOOL_NAME", "search_files"),
        ("EXEC_TOOL_NAME", "exec"),
        ("GIT_STATUS_TOOL_NAME", "git_status"),
        ("GIT_DIFF_TOOL_NAME", "git_diff"),
        ("GIT_LOG_TOOL_NAME", "git_log"),
        ("GIT_SHOW_TOOL_NAME", "git_show"),
    ]
    vars.update({k: tool_name if tool_name in tool_names else "" for k, tool_name in tool_mapping})

    vars["CAPABILITY_SUMMARY"] = format_capability_summary(capabilities, policy_flags)
    return vars


def format_mcp_tools_list(tool_names: list[str]) -> str:
    return ", ".join(tool_names)


def bool_to_template_value(value: bool) -> str:
    return "true" if value else ""


def format_capability_summary(capabilities: CapabilitySet, policy_flags: PolicyFlagSet) -> str:
    cap_lines = sorted(cap.value for cap in capabilities)
    cap_section = "\n".join(f"  - {line}" for line in cap_lines) or "  (none)"
    flag_lines = sorted(flag.value for flag in policy_flags)
    flag_section = "\n".join(f"  - {line}" for line in flag_lines) or "  (none)"
    return f"Capabilities:\n{cap_section}\n\nPolicy Flags:\n{flag_section}"
