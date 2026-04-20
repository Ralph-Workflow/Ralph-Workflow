"""Workspace tool handlers for MCP interactions.

Ports the Rust ``mcp_server::tool_workspace`` helpers into Python so MCP
handlers can read, list, search, and write workspace files while enforcing
session capabilities and edit area policies.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, cast

from ralph.mcp.policy_outcomes import is_policy_approved
from ralph.mcp.tool_coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    SessionLike,
    ToolContent,
    ToolError,
    ToolResult,
    require_capability,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.workspace import Workspace

WORKSPACE_READ_CAPABILITY = "WorkspaceRead"
WORKSPACE_WRITE_TRACKED_CAPABILITY = "WorkspaceWriteTracked"
WORKSPACE_WRITE_EPHEMERAL_CAPABILITY = "WorkspaceWriteEphemeral"
_RECURSIVE_SKIP_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".venv",
        "__pycache__",
        "node_modules",
        "target",
    }
)


def _attribute_value(
    obj: object, attribute_name: str, default: object | None = None
) -> object | None:
    return cast("object | None", getattr(obj, attribute_name, default))


def required_string_param(params: dict[str, object], name: str) -> str:
    """Return a required string parameter, raising if it is missing."""

    value = params.get(name)
    if not isinstance(value, str):
        raise InvalidParamsError(f"Missing '{name}' parameter")
    return value


def _normalize_relative_path(path: str) -> str:
    normalized = str(PurePosixPath(path))
    if normalized in ("", "."):
        return ""
    return normalized


def _join_path(base: str, entry: str) -> str:
    if not base:
        return _normalize_relative_path(entry)
    return _normalize_relative_path(str(PurePosixPath(base) / entry))


def _list_dir_entries(workspace: Workspace, path: str) -> list[str]:
    try:
        return workspace.list_dir(path)
    except Exception as exc:
        raise ToolError(f"Failed to list directory '{path}': {exc}") from exc


def _is_policy_approved(outcome: object | None) -> bool:
    return is_policy_approved(outcome)


def _is_parallel_worker(session: object) -> bool:
    flag = _attribute_value(session, "is_parallel_worker", False)
    if callable(flag):
        try:
            executable = cast("Callable[[], object]", flag)
            return bool(executable())
        except TypeError:
            return False
    return bool(flag)


def _check_edit_area_restriction(session: object, path: str) -> None:
    if not _is_parallel_worker(session):
        return
    checker = _attribute_value(session, "check_edit_area")
    if not callable(checker):
        return
    callable_checker = cast("Callable[[str], object]", checker)
    outcome = callable_checker(path)
    if _is_policy_approved(outcome):
        return
    raise CapabilityDeniedError(f"Write to '{path}' denied: edit area restriction")


def _write_file_to_workspace(workspace: Workspace, path: str, content: str) -> None:
    try:
        workspace.write(path, content)
    except Exception as exc:
        raise ToolError(f"Failed to write file '{path}': {exc}") from exc


def _is_path_git_tracked(workspace: Workspace, path: str) -> bool:
    normalized = _normalize_relative_path(path)
    if not normalized:
        return False
    if not workspace.exists(normalized):
        return False
    candidate = normalized.replace("\\", "/")
    return (
        ".agent/" not in candidate
        and "/target/" not in candidate
        and "node_modules/" not in candidate
    )


def _list_dir_flat(workspace: Workspace, path: str) -> str:
    normalized = _normalize_relative_path(path)
    entries = _list_dir_entries(workspace, normalized)
    output = f"Directory: {path}\n"
    for entry in sorted(entries):
        entry_path = _join_path(normalized, entry)
        entry_type = "[DIR]" if workspace.is_dir(entry_path) else "[FILE]"
        output += f"  {entry_type} {entry_path}\n"
    return output


def _should_recurse_into_directory(workspace: Workspace, entry_path: str) -> bool:
    entry_name = PurePosixPath(entry_path).name
    if entry_name in _RECURSIVE_SKIP_DIRECTORY_NAMES:
        return False
    return not workspace.exists(_join_path(entry_path, ".git"))


def _append_dir_entry(workspace: Workspace, entry_path: str, output: list[str], depth: int) -> None:
    indent = "  " * depth
    is_dir = workspace.is_dir(entry_path)
    entry_type = "[DIR]" if is_dir else "[FILE]"
    output.append(f"{indent}{entry_type} {entry_path}\n")
    if is_dir and _should_recurse_into_directory(workspace, entry_path):
        _walk_directory_recursive(workspace, entry_path, output, depth + 1)


def _walk_directory_recursive(
    workspace: Workspace,
    path: str,
    output: list[str],
    depth: int,
) -> None:
    entries = _list_dir_entries(workspace, path)
    for entry in sorted(entries):
        entry_path = _join_path(path, entry)
        _append_dir_entry(workspace, entry_path, output, depth)


def _collect_matching_files(workspace: Workspace, base_path: str, pattern: str) -> list[str]:
    matches: list[str] = []
    entries = _list_dir_entries(workspace, base_path)
    for entry in sorted(entries):
        entry_path = _join_path(base_path, entry)
        if workspace.is_dir(entry_path):
            if _should_recurse_into_directory(workspace, entry_path):
                matches.extend(_collect_matching_files(workspace, entry_path, pattern))
        elif workspace.is_file(entry_path):
            filename = entry
            if pattern == "*" or pattern in filename:
                matches.append(entry_path)
    return matches


def handle_read_file(
    session: SessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Workspace read")
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)
    try:
        content = workspace.read(normalized)
    except FileNotFoundError as exc:
        raise ToolError(f"Failed to read file '{path}': {exc}") from exc
    except Exception as exc:
        raise ToolError(f"Failed to read file '{path}': {exc}") from exc
    return ToolResult(content=[ToolContent.text_content(content)], is_error=False)


def handle_list_directory(
    session: SessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Directory listing")
    path = required_string_param(params, "path")
    recursive = bool(params.get("recursive", False))
    output = (
        _list_dir_flat(workspace, path)
        if not recursive
        else _list_dir_recursive_output(workspace, path)
    )
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


def _list_dir_recursive_output(workspace: Workspace, path: str) -> str:
    normalized = _normalize_relative_path(path)
    output_lines: list[str] = [f"Directory (recursive): {path}\n"]
    _walk_directory_recursive(workspace, normalized, output_lines, 0)
    return "".join(output_lines)


def handle_list_directory_recursive(
    session: SessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Recursive directory listing")
    path = required_string_param(params, "path")
    output = _list_dir_recursive_output(workspace, path)
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


def handle_search_files(
    session: SessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    require_capability(session, WORKSPACE_READ_CAPABILITY, "File search")
    pattern = required_string_param(params, "pattern")
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)
    matches = _collect_matching_files(workspace, normalized, pattern)
    matches.sort()
    output = [f"Search pattern: '{pattern}' in path: {path}\nFiles found:\n"]
    if matches:
        output.extend(f"  {match}\n" for match in matches)
    output.append("\nNote: Use exec with grep for actual content search")
    return ToolResult(content=[ToolContent.text_content("".join(output))], is_error=False)


def handle_write_file(
    session: SessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    path = required_string_param(params, "path")
    content = required_string_param(params, "content")
    normalized = _normalize_relative_path(path)
    _check_edit_area_restriction(session, normalized)
    is_tracked = _is_path_git_tracked(workspace, normalized)
    capability = (
        WORKSPACE_WRITE_TRACKED_CAPABILITY if is_tracked else WORKSPACE_WRITE_EPHEMERAL_CAPABILITY
    )
    require_capability(session, capability, "Workspace write")
    _write_file_to_workspace(workspace, normalized, content)
    return ToolResult(
        content=[ToolContent.text_content(f"Successfully wrote {len(content)} bytes to {path}")],
        is_error=False,
    )


__all__ = [
    "handle_list_directory",
    "handle_list_directory_recursive",
    "handle_read_file",
    "handle_search_files",
    "handle_write_file",
    "required_string_param",
]
