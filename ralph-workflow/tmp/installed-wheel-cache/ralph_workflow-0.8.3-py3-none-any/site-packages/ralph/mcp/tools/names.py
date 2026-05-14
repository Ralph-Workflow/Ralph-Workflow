"""Canonical Ralph MCP tool naming helpers."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

RALPH_MCP_SERVER_NAME = "ralph"


class RalphToolName(StrEnum):
    """Canonical names for all Ralph MCP tools."""

    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    LIST_DIRECTORY = "list_directory"
    LIST_DIRECTORY_RECURSIVE = "list_directory_recursive"
    DIRECTORY_TREE = "directory_tree"
    SEARCH_FILES = "search_files"
    READ_MULTIPLE_FILES = "read_multiple_files"
    STAT_PATH = "stat_path"
    LIST_ALLOWED_ROOTS = "list_allowed_roots"
    GREP_FILES = "grep_files"
    EDIT_FILE = "edit_file"
    APPEND_FILE = "append_file"
    CREATE_DIRECTORY = "create_directory"
    MOVE_FILE = "move_file"
    COPY_FILE = "copy_file"
    DELETE_PATH = "delete_path"
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
    WEB_SEARCH = "web_search"
    VISIT_URL = "visit_url"
    READ_IMAGE = "read_image"
    READ_MEDIA = "read_media"

    def with_prefix(self, *, tool_name_prefix: str = "") -> str:
        """Return the tool name with an optional prefix applied."""
        return f"{tool_name_prefix}{self}" if tool_name_prefix else self.value

    def as_claude_alias(self, *, server_name: str = RALPH_MCP_SERVER_NAME) -> str:
        """Return the Claude MCP tool alias in the form mcp__<server>__<tool>."""
        return f"mcp__{server_name}__{self}"

    def prompt_aliases(self, *, tool_name_prefix: str = "") -> tuple[str, ...]:
        """Return the full set of prompt-facing alias names for this tool."""
        primary = self.with_prefix(tool_name_prefix=tool_name_prefix)
        if primary == self.value:
            return (self.value,)
        return (primary, self.value)

    def prompt_reference(self, *, tool_name_prefix: str = "") -> str:
        """Return a human-readable reference string for prompts."""
        aliases = self.prompt_aliases(tool_name_prefix=tool_name_prefix)
        if len(aliases) == 1:
            return f"`{aliases[0]}`"
        return f"`{aliases[0]}` or bare `{aliases[1]}`"


READ_FILE_TOOL = RalphToolName.READ_FILE
WRITE_FILE_TOOL = RalphToolName.WRITE_FILE
LIST_DIRECTORY_TOOL = RalphToolName.LIST_DIRECTORY
LIST_DIRECTORY_RECURSIVE_TOOL = RalphToolName.LIST_DIRECTORY_RECURSIVE
DIRECTORY_TREE_TOOL = RalphToolName.DIRECTORY_TREE
SEARCH_FILES_TOOL = RalphToolName.SEARCH_FILES
READ_MULTIPLE_FILES_TOOL = RalphToolName.READ_MULTIPLE_FILES
STAT_PATH_TOOL = RalphToolName.STAT_PATH
LIST_ALLOWED_ROOTS_TOOL = RalphToolName.LIST_ALLOWED_ROOTS
GREP_FILES_TOOL = RalphToolName.GREP_FILES
EDIT_FILE_TOOL = RalphToolName.EDIT_FILE
APPEND_FILE_TOOL = RalphToolName.APPEND_FILE
CREATE_DIRECTORY_TOOL = RalphToolName.CREATE_DIRECTORY
MOVE_FILE_TOOL = RalphToolName.MOVE_FILE
COPY_FILE_TOOL = RalphToolName.COPY_FILE
DELETE_PATH_TOOL = RalphToolName.DELETE_PATH
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
WEB_SEARCH_TOOL = RalphToolName.WEB_SEARCH
VISIT_URL_TOOL = RalphToolName.VISIT_URL
READ_IMAGE_TOOL = RalphToolName.READ_IMAGE
READ_MEDIA_TOOL = RalphToolName.READ_MEDIA

WORKSPACE_READ_TOOLS: tuple[str, ...] = (
    READ_FILE_TOOL,
    LIST_DIRECTORY_TOOL,
    LIST_DIRECTORY_RECURSIVE_TOOL,
    DIRECTORY_TREE_TOOL,
    SEARCH_FILES_TOOL,
    READ_MULTIPLE_FILES_TOOL,
    STAT_PATH_TOOL,
    LIST_ALLOWED_ROOTS_TOOL,
    GREP_FILES_TOOL,
)
WORKSPACE_EDIT_TOOLS: tuple[str, ...] = (
    EDIT_FILE_TOOL,
    APPEND_FILE_TOOL,
    CREATE_DIRECTORY_TOOL,
    MOVE_FILE_TOOL,
    COPY_FILE_TOOL,
)
WORKSPACE_DELETE_TOOLS: tuple[str, ...] = (DELETE_PATH_TOOL,)
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
WEB_SEARCH_TOOLS: tuple[str, ...] = (WEB_SEARCH_TOOL,)
WEB_VISIT_TOOLS: tuple[str, ...] = (VISIT_URL_TOOL,)
MEDIA_READ_TOOLS: tuple[str, ...] = (READ_IMAGE_TOOL, READ_MEDIA_TOOL)

ALL_RALPH_TOOLS: tuple[str, ...] = (
    *WORKSPACE_READ_TOOLS,
    *WORKSPACE_EDIT_TOOLS,
    *WORKSPACE_DELETE_TOOLS,
    *GIT_STATUS_READ_TOOLS,
    *GIT_DIFF_READ_TOOLS,
    *TRACKED_WRITE_TOOLS,
    *PROCESS_EXEC_TOOLS,
    *ARTIFACT_TOOLS,
    *PLANNING_DRAFT_TOOLS,
    *PROGRESS_TOOLS,
    *ENV_READ_TOOLS,
    *WEB_SEARCH_TOOLS,
    *WEB_VISIT_TOOLS,
)

# Authoritative source: https://opencode.ai/config.json schema PermissionConfig keys
# Setting each to false physically removes the tool (unlike permission which is allow-by-default).
OPENCODE_NATIVE_TOOLS_TO_DISABLE: tuple[str, ...] = (
    "bash",
    "codesearch",
    "edit",
    "glob",
    "grep",
    "list",
    "lsp",
    "patch",
    "question",
    "read",
    "skill",
    "task",
    "todowrite",
    "webfetch",
    "websearch",
    "write",
)

# Authoritative source: https://developers.openai.com/codex/config-reference
# apply_patch and core editing primitives are NOT disableable — documented limitation.
CODEX_NATIVE_FEATURES_TO_DISABLE: tuple[tuple[str, str], ...] = (
    ("features.shell_tool", "false"),
    ("features.multi_agent", "false"),
    ("features.undo", "false"),
    ("features.apps", "false"),
    ("web_search", '"disabled"'),
)


def _coerce_tool_name(tool_name: str | RalphToolName) -> RalphToolName | None:
    if isinstance(tool_name, RalphToolName):
        return tool_name
    try:
        return RalphToolName(tool_name)
    except ValueError:
        return None


def prefix_tool_name(tool_name: str | RalphToolName, *, tool_name_prefix: str = "") -> str:
    """Return the tool name with an optional prefix applied."""
    coerced = _coerce_tool_name(tool_name)
    if coerced is not None:
        return coerced.with_prefix(tool_name_prefix=tool_name_prefix)
    return f"{tool_name_prefix}{tool_name}" if tool_name_prefix else tool_name


def prefix_tool_names(
    tool_names: Sequence[str | RalphToolName],
    *,
    tool_name_prefix: str = "",
) -> list[str]:
    """Apply the given prefix to each tool name in the sequence."""
    return [
        prefix_tool_name(tool_name, tool_name_prefix=tool_name_prefix) for tool_name in tool_names
    ]


def claude_tool_name(
    tool_name: str | RalphToolName, *, server_name: str = RALPH_MCP_SERVER_NAME
) -> str:
    """Return the Claude MCP alias for a tool name (`mcp__<server>__<tool>`)."""
    # Claude exposes every MCP tool as `mcp__<server>__<tool>`. This helper is the
    # canonical alias builder used by prompts/tests so transport-specific naming does
    # not drift from the runtime CLI wiring.
    coerced = _coerce_tool_name(tool_name)
    if coerced is not None:
        return coerced.as_claude_alias(server_name=server_name)
    return f"mcp__{server_name}__{tool_name}"


def claude_tool_name_prefix(*, server_name: str = RALPH_MCP_SERVER_NAME) -> str:
    """Return the `mcp__<server>__` prefix string used by Claude for MCP tools."""
    return f"mcp__{server_name}__"


def upstream_proxy_tool_name(server_name: str, tool_name: str) -> str:
    """Return the stable proxy alias for an upstream server's tool."""
    return f"ralph_upstream__{server_name}__{tool_name}"
