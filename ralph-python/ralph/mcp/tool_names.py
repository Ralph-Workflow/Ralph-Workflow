"""Canonical Ralph MCP tool naming helpers."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

RALPH_MCP_SERVER_NAME = "ralph"


class RalphToolName(StrEnum):
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    LIST_DIRECTORY = "list_directory"
    LIST_DIRECTORY_RECURSIVE = "list_directory_recursive"
    SEARCH_FILES = "search_files"
    GIT_STATUS = "git_status"
    GIT_DIFF = "git_diff"
    GIT_LOG = "git_log"
    GIT_SHOW = "git_show"
    EXEC = "exec"
    SUBMIT_ARTIFACT = "ralph_submit_artifact"
    SUBMIT_PLAN_SECTION = "ralph_submit_plan_section"
    FINALIZE_PLAN = "ralph_finalize_plan"
    GET_PLAN_DRAFT = "ralph_get_plan_draft"
    DISCARD_PLAN_DRAFT = "ralph_discard_plan_draft"
    REPORT_PROGRESS = "report_progress"
    DECLARE_COMPLETE = "declare_complete"
    COORDINATE = "coordinate"
    READ_ENV = "read_env"


READ_FILE_TOOL = RalphToolName.READ_FILE
WRITE_FILE_TOOL = RalphToolName.WRITE_FILE
LIST_DIRECTORY_TOOL = RalphToolName.LIST_DIRECTORY
LIST_DIRECTORY_RECURSIVE_TOOL = RalphToolName.LIST_DIRECTORY_RECURSIVE
SEARCH_FILES_TOOL = RalphToolName.SEARCH_FILES
GIT_STATUS_TOOL = RalphToolName.GIT_STATUS
GIT_DIFF_TOOL = RalphToolName.GIT_DIFF
GIT_LOG_TOOL = RalphToolName.GIT_LOG
GIT_SHOW_TOOL = RalphToolName.GIT_SHOW
EXEC_TOOL = RalphToolName.EXEC
SUBMIT_ARTIFACT_TOOL = RalphToolName.SUBMIT_ARTIFACT
SUBMIT_PLAN_SECTION_TOOL = RalphToolName.SUBMIT_PLAN_SECTION
FINALIZE_PLAN_TOOL = RalphToolName.FINALIZE_PLAN
GET_PLAN_DRAFT_TOOL = RalphToolName.GET_PLAN_DRAFT
DISCARD_PLAN_DRAFT_TOOL = RalphToolName.DISCARD_PLAN_DRAFT
REPORT_PROGRESS_TOOL = RalphToolName.REPORT_PROGRESS
DECLARE_COMPLETE_TOOL = RalphToolName.DECLARE_COMPLETE
COORDINATE_TOOL = RalphToolName.COORDINATE
READ_ENV_TOOL = RalphToolName.READ_ENV

WORKSPACE_READ_TOOLS: tuple[str, ...] = (
    READ_FILE_TOOL,
    LIST_DIRECTORY_TOOL,
    LIST_DIRECTORY_RECURSIVE_TOOL,
    SEARCH_FILES_TOOL,
)
GIT_STATUS_READ_TOOLS: tuple[str, ...] = (GIT_STATUS_TOOL, GIT_LOG_TOOL, GIT_SHOW_TOOL)
GIT_DIFF_READ_TOOLS: tuple[str, ...] = (GIT_DIFF_TOOL,)
TRACKED_WRITE_TOOLS: tuple[str, ...] = (WRITE_FILE_TOOL,)
PROCESS_EXEC_TOOLS: tuple[str, ...] = (EXEC_TOOL,)
ARTIFACT_TOOLS: tuple[str, ...] = (
    SUBMIT_ARTIFACT_TOOL,
    DECLARE_COMPLETE_TOOL,
    COORDINATE_TOOL,
)
PLANNING_DRAFT_TOOLS: tuple[str, ...] = (
    SUBMIT_PLAN_SECTION_TOOL,
    FINALIZE_PLAN_TOOL,
    GET_PLAN_DRAFT_TOOL,
    DISCARD_PLAN_DRAFT_TOOL,
)
PROGRESS_TOOLS: tuple[str, ...] = (REPORT_PROGRESS_TOOL,)
ENV_READ_TOOLS: tuple[str, ...] = (READ_ENV_TOOL,)

ALL_RALPH_TOOLS: tuple[str, ...] = (
    *WORKSPACE_READ_TOOLS,
    *GIT_STATUS_READ_TOOLS,
    *GIT_DIFF_READ_TOOLS,
    *TRACKED_WRITE_TOOLS,
    *PROCESS_EXEC_TOOLS,
    *ARTIFACT_TOOLS,
    *PLANNING_DRAFT_TOOLS,
    *PROGRESS_TOOLS,
    *ENV_READ_TOOLS,
)


def prefix_tool_name(tool_name: str | RalphToolName, *, tool_name_prefix: str = "") -> str:
    return f"{tool_name_prefix}{tool_name}" if tool_name_prefix else tool_name


def prefix_tool_names(
    tool_names: Sequence[str | RalphToolName],
    *,
    tool_name_prefix: str = "",
) -> list[str]:
    return [
        prefix_tool_name(tool_name, tool_name_prefix=tool_name_prefix) for tool_name in tool_names
    ]


def claude_tool_name(
    tool_name: str | RalphToolName, *, server_name: str = RALPH_MCP_SERVER_NAME
) -> str:
    return f"mcp__{server_name}__{tool_name}"


def claude_tool_name_prefix(*, server_name: str = RALPH_MCP_SERVER_NAME) -> str:
    return f"mcp__{server_name}__"


def claude_allowed_tool_names(
    tool_names: Iterable[str | RalphToolName] = ALL_RALPH_TOOLS,
    *,
    server_name: str = RALPH_MCP_SERVER_NAME,
) -> str:
    return ",".join(
        claude_tool_name(tool_name, server_name=server_name) for tool_name in tool_names
    )
