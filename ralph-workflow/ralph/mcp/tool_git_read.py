"""Tool git read handlers - compatibility wrappers over the sub-package."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.tools import git_read as _impl
from ralph.mcp.tools.git_read import (
    _DEFAULT_LOG_COUNT,
    GIT_DIFF_READ_CAPABILITY,
    GIT_STATUS_READ_CAPABILITY,
    ExecutionError,
    WorkspaceWithRoot,
    parse_git_diff_params,
    parse_git_log_params,
    parse_git_show_params,
    run_git_command,
    run_git_command_lenient,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.tools.coordination import SessionLike, ToolResult


def handle_git_status(
    session: SessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    _impl.run_git_command = run_git_command
    return _impl.handle_git_status(session, workspace, params)



def handle_git_diff(
    session: SessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    _impl.run_git_command_lenient = run_git_command_lenient
    return _impl.handle_git_diff(session, workspace, params)



def handle_git_log(
    session: SessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    _impl.run_git_command = run_git_command
    return _impl.handle_git_log(session, workspace, params)



def handle_git_show(
    session: SessionLike,
    workspace: object,
    params: Mapping[str, object],
) -> ToolResult:
    _impl.run_git_command = run_git_command
    return _impl.handle_git_show(session, workspace, params)


__all__ = [
    "GIT_DIFF_READ_CAPABILITY",
    "GIT_STATUS_READ_CAPABILITY",
    "_DEFAULT_LOG_COUNT",
    "ExecutionError",
    "WorkspaceWithRoot",
    "handle_git_diff",
    "handle_git_log",
    "handle_git_show",
    "handle_git_status",
    "parse_git_diff_params",
    "parse_git_log_params",
    "parse_git_show_params",
    "run_git_command",
    "run_git_command_lenient",
]
