"""Write, edit, append, create, move, copy, and delete handler functions."""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolError,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.workspace._utils import (
    WORKSPACE_DELETE_CAPABILITY,
    WORKSPACE_EDIT_CAPABILITY,
    WORKSPACE_WRITE_EPHEMERAL_CAPABILITY,
    WORKSPACE_WRITE_TRACKED_CAPABILITY,
    _tool_json,
    _write_file_to_workspace,
    check_edit_area_restriction,
    is_path_git_tracked,
    normalize_relative_path,
    required_string_param,
)

if TYPE_CHECKING:
    from ralph.workspace import Workspace


def handle_write_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Write UTF-8 content to a workspace file, creating it if necessary."""
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)
    check_edit_area_restriction(session, normalized)
    is_tracked = is_path_git_tracked(workspace, normalized)
    capability = (
        WORKSPACE_WRITE_TRACKED_CAPABILITY if is_tracked else WORKSPACE_WRITE_EPHEMERAL_CAPABILITY
    )
    require_capability(session, capability, "Workspace write")
    content = required_string_param(params, "content")
    _write_file_to_workspace(workspace, normalized, content)
    return ToolResult(
        content=[ToolContent.text_content(f"Successfully wrote {len(content)} bytes to {path}")],
        is_error=False,
    )


def handle_edit_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Apply structured oldText/newText replacements to a workspace file."""
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)
    check_edit_area_restriction(session, normalized)
    require_capability(session, WORKSPACE_EDIT_CAPABILITY, "Workspace edit")
    edits_param = params.get("edits")
    if not isinstance(edits_param, list) or len(edits_param) == 0:
        raise InvalidParamsError("Missing 'edits' parameter as non-empty list")
    edits = cast("list[dict[str, str]]", edits_param)
    dry_run = bool(params.get("dry_run", False))

    try:
        original_content = workspace.read(normalized)
    except FileNotFoundError:
        original_content = ""

    current_content = original_content
    applied_edits: list[dict[str, str]] = []

    for i, edit in enumerate(edits):
        old_text = edit.get("oldText")
        new_text = edit.get("newText", "")
        if not isinstance(old_text, str):
            raise InvalidParamsError(f"Edit {i}: missing 'oldText' string")

        idx = current_content.find(old_text)
        if idx == -1:
            diff = difflib.unified_diff(
                original_content.splitlines(keepends=True),
                current_content.splitlines(keepends=True),
                fromfile=path,
                tofile=path,
                lineterm="",
            )
            return ToolResult(
                content=[
                    ToolContent.text_content(
                        _tool_json(
                            {
                                "status": "no_match",
                                "edit_index": i,
                                "preview": "".join(diff),
                            }
                        )
                    )
                ],
                is_error=True,
            )
        current_content = current_content[:idx] + new_text + current_content[idx + len(old_text) :]
        applied_edits.append({"oldText": old_text, "newText": new_text})

    diff = difflib.unified_diff(
        original_content.splitlines(keepends=True),
        current_content.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
        lineterm="",
    )

    if dry_run:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "preview",
                            "diff": "".join(diff),
                            "edits_applied": len(applied_edits),
                        }
                    )
                )
            ],
            is_error=False,
        )

    try:
        workspace.write(normalized, current_content)
    except Exception as exc:
        raise ToolError(f"Failed to write file '{path}': {exc}") from exc

    return ToolResult(
        content=[
            ToolContent.text_content(
                _tool_json(
                    {
                        "status": "applied",
                        "diff": "".join(diff),
                        "bytes_written": len(current_content),
                    }
                )
            )
        ],
        is_error=False,
    )


def handle_append_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Append content to a workspace file."""
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)
    check_edit_area_restriction(session, normalized)
    require_capability(session, WORKSPACE_EDIT_CAPABILITY, "Workspace append")
    content = required_string_param(params, "content")

    try:
        workspace.append(normalized, content)
    except Exception as exc:
        raise ToolError(f"Failed to append to file '{path}': {exc}") from exc

    return ToolResult(
        content=[
            ToolContent.text_content(
                _tool_json(
                    {
                        "path": path,
                        "bytes_appended": len(content),
                    }
                )
            )
        ],
        is_error=False,
    )


def handle_create_directory(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Create a directory (and parents) within the workspace."""
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)
    check_edit_area_restriction(session, normalized)
    require_capability(session, WORKSPACE_EDIT_CAPABILITY, "Create directory")

    try:
        workspace.mkdirs(normalized)
    except Exception as exc:
        raise ToolError(f"Failed to create directory '{path}': {exc}") from exc

    return ToolResult(
        content=[
            ToolContent.text_content(
                _tool_json(
                    {
                        "path": path,
                        "created": True,
                    }
                )
            )
        ],
        is_error=False,
    )


def handle_move_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Move or rename a workspace file or directory."""
    src = required_string_param(params, "src")
    dest = required_string_param(params, "dest")
    src_norm = normalize_relative_path(src)
    dest_norm = normalize_relative_path(dest)
    check_edit_area_restriction(session, src_norm)
    check_edit_area_restriction(session, dest_norm)
    require_capability(session, WORKSPACE_EDIT_CAPABILITY, "Move file")
    overwrite = bool(params.get("overwrite", False))

    try:
        workspace.move(src_norm, dest_norm, overwrite=overwrite)
    except FileExistsError:
        raise ToolError(f"Destination '{dest}' already exists") from None
    except Exception as exc:
        raise ToolError(f"Failed to move '{src}' to '{dest}': {exc}") from exc

    return ToolResult(
        content=[
            ToolContent.text_content(
                _tool_json(
                    {
                        "src": src,
                        "dest": dest,
                    }
                )
            )
        ],
        is_error=False,
    )


def handle_copy_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Copy a workspace file or directory to a new location."""
    src = required_string_param(params, "src")
    dest = required_string_param(params, "dest")
    src_norm = normalize_relative_path(src)
    dest_norm = normalize_relative_path(dest)
    check_edit_area_restriction(session, dest_norm)
    require_capability(session, WORKSPACE_EDIT_CAPABILITY, "Copy file")
    overwrite = bool(params.get("overwrite", False))

    try:
        workspace.copy(src_norm, dest_norm, overwrite=overwrite)
    except FileExistsError:
        raise ToolError(f"Destination '{dest}' already exists") from None
    except Exception as exc:
        raise ToolError(f"Failed to copy '{src}' to '{dest}': {exc}") from exc

    return ToolResult(
        content=[
            ToolContent.text_content(
                _tool_json(
                    {
                        "src": src,
                        "dest": dest,
                    }
                )
            )
        ],
        is_error=False,
    )


def handle_delete_path(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Delete a workspace file or directory."""
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)
    check_edit_area_restriction(session, normalized)
    require_capability(session, WORKSPACE_DELETE_CAPABILITY, "Delete path")
    recursive = bool(params.get("recursive", False))

    try:
        workspace.delete(normalized, recursive=recursive)
    except IsADirectoryError:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Path '{path}' is a directory, use recursive=True to delete"
                )
            ],
            is_error=True,
        )
    except FileNotFoundError:
        raise ToolError(f"Path '{path}' not found") from None
    except Exception as exc:
        raise ToolError(f"Failed to delete '{path}': {exc}") from exc

    return ToolResult(
        content=[
            ToolContent.text_content(
                _tool_json(
                    {
                        "path": path,
                        "deleted": True,
                        "recursive": recursive,
                    }
                )
            )
        ],
        is_error=False,
    )
