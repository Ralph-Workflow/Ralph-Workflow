"""Read, stat, list, and search handler functions."""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING

from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolError,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.workspace._list_ops import (
    _collect_matching_files,
    _list_dir_recursive_output,
    list_dir_flat,
)
from ralph.mcp.tools.workspace._utils import (
    _GREP_DEFAULT_LIMIT,
    FULL_READ_DEFAULT_MAX_BYTES,
    WORKSPACE_METADATA_READ_CAPABILITY,
    WORKSPACE_READ_CAPABILITY,
    _int_opt_param,
    _int_param,
    _ReadSelector,
    _tool_json,
    join_path,
    normalize_relative_path,
    required_string_param,
)
from ralph.workspace.skip import RECURSIVE_SKIP_DIRECTORY_NAMES

if TYPE_CHECKING:
    from ralph.workspace import Workspace


def _dispatch_partial_read(
    workspace: Workspace,
    normalized: str,
    path: str,
    sel: _ReadSelector,
) -> ToolResult:
    """Validate selectors and dispatch to byte-window or line-based partial read."""
    line_range = (sel.start is not None) or (sel.end is not None)
    byte_window = (sel.off is not None and sel.off > 0) or (sel.lim is not None)
    head_mode = sel.head is not None
    tail_mode = sel.tail is not None

    if line_range and byte_window:
        raise InvalidParamsError("Cannot combine line_start/line_end with offset/limit")
    if byte_window and (head_mode or tail_mode):
        raise InvalidParamsError("Cannot combine offset/limit with head/tail")
    if line_range and (head_mode or tail_mode):
        raise InvalidParamsError("Cannot combine line_start/line_end with head/tail")

    if byte_window:
        byte_offset = sel.off if sel.off is not None else 0
        content, meta = workspace.read_bytes(normalized, offset=byte_offset, limit=sel.lim)
        payload: dict[str, object] = {
            "path": path,
            "content": content,
            "total_bytes": meta.get("total_bytes"),
            "returned_bytes": meta.get("returned_bytes"),
            "truncated": meta.get("truncated"),
        }
    else:
        content, meta = workspace.read_lines(
            normalized, start=sel.start, end=sel.end, head=sel.head, tail=sel.tail
        )
        payload = {
            "path": path,
            "content": content,
            "total_lines": meta.get("total_lines"),
            "returned_lines": meta.get("returned_lines"),
            "truncated": meta.get("truncated"),
        }
    return ToolResult(content=[ToolContent.text_content(_tool_json(payload))], is_error=False)


def handle_read_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Read a UTF-8 file from the workspace.

    Full-file reads (no partial params) return a plain text block for UTF-8 files
    at or below max_bytes (default 5_000_000). The JSON envelope only appears when
    truncated is True OR when an error occurs (binary_or_invalid_utf8).

    Partial-read parameter groups (line_start/line_end, offset/limit, head, tail)
    are mutually exclusive; combining any two raises InvalidParams.

    Optional param max_bytes overrides the default ceiling for full-file reads.
    """
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Workspace read")
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)

    sel = _ReadSelector.from_params(params)
    if sel.is_active():
        return _dispatch_partial_read(workspace, normalized, path, sel)

    max_bytes = _int_param(params, "max_bytes", FULL_READ_DEFAULT_MAX_BYTES)
    try:
        stat_result: dict[str, object] = workspace.stat(normalized)
    except Exception:
        stat_result = {}

    file_type = stat_result.get("type", "")
    size_bytes = stat_result.get("size_bytes")

    if file_type == "file" and isinstance(size_bytes, int) and size_bytes > max_bytes:
        head_value = max(1, max_bytes // 256)
        content, _meta = workspace.read_lines(normalized, head=head_value)
        payload = {
            "path": path,
            "content": content,
            "truncated": True,
            "total_bytes": size_bytes,
            "max_bytes": max_bytes,
            "reason": "oversize",
        }
        return ToolResult(content=[ToolContent.text_content(_tool_json(payload))], is_error=False)

    try:
        content = workspace.read(normalized)
    except UnicodeDecodeError as exc:
        payload = {
            "status": "binary_or_invalid_utf8",
            "path": path,
            "error": str(exc),
            "byte_offset": exc.start,
        }
        return ToolResult(content=[ToolContent.text_content(_tool_json(payload))], is_error=True)
    except FileNotFoundError as exc:
        raise ToolError(f"Failed to read file '{path}': {exc}") from exc
    except Exception as exc:
        raise ToolError(f"Failed to read file '{path}': {exc}") from exc
    return ToolResult(content=[ToolContent.text_content(content)], is_error=False)


def handle_read_multiple_files(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Read multiple workspace files in one call and return per-file results."""
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Read multiple files")
    paths_param = params.get("paths")
    if not isinstance(paths_param, list):
        raise InvalidParamsError("Missing 'paths' parameter as list of strings")
    paths = [str(p) for p in paths_param]

    results: list[dict[str, object]] = []
    for p in paths:
        normalized = normalize_relative_path(p)
        try:
            content = workspace.read(normalized)
            results.append({"path": p, "content": content})
        except Exception as exc:
            results.append({"path": p, "error": str(exc)})

    payload = _tool_json({"files": results})
    return ToolResult(content=[ToolContent.text_content(payload)], is_error=False)


def handle_stat(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Return file metadata (type, size, timestamps) for a workspace path."""
    require_capability(session, WORKSPACE_METADATA_READ_CAPABILITY, "Workspace metadata read")
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)
    try:
        stat_result = workspace.stat(normalized)
    except Exception as exc:
        raise ToolError(f"Failed to stat '{path}': {exc}") from exc
    return ToolResult(content=[ToolContent.text_content(_tool_json(stat_result))], is_error=False)


def handle_list_allowed_roots(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Return the list of workspace paths the session is permitted to access."""
    require_capability(session, WORKSPACE_READ_CAPABILITY, "List allowed roots")
    try:
        roots = workspace.allowed_roots()
    except Exception as exc:
        raise ToolError(f"Failed to list allowed roots: {exc}") from exc
    payload = _tool_json({"allowed_roots": roots})
    return ToolResult(content=[ToolContent.text_content(payload)], is_error=False)


def handle_list_directory(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """List entries in a workspace directory, optionally recursive."""
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Directory listing")
    path = required_string_param(params, "path")
    recursive = bool(params.get("recursive", False))
    output = (
        list_dir_flat(workspace, path)
        if not recursive
        else _list_dir_recursive_output(workspace, path)
    )
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


def handle_list_directory_recursive(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Return a flat listing of all entries under a workspace directory."""
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Recursive directory listing")
    path = required_string_param(params, "path")
    output = _list_dir_recursive_output(workspace, path)
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


def _build_directory_tree(
    workspace: Workspace,
    path: str,
    current_depth: int,
    max_depth: int | None,
    exclude_patterns: list[str] | None,
) -> dict[str, object]:
    """Build a recursive directory tree structure."""
    normalized = normalize_relative_path(path)
    name = normalized.split("/")[-1] if normalized else path

    def should_exclude(entry_name: str, entry_path: str) -> bool:
        if not exclude_patterns:
            return False
        for pat in exclude_patterns:
            if fnmatch.fnmatchcase(entry_name, pat) or fnmatch.fnmatchcase(entry_path, pat):
                return True
        return False

    is_dir = workspace.is_dir(normalized)
    if not is_dir:
        return {"name": name, "type": "file"}

    if max_depth is not None and current_depth >= max_depth:
        return {"name": name, "type": "dir", "children": []}

    entries: list[dict[str, object]] = []
    try:
        dir_entries = workspace.list_dir(normalized)
    except Exception:
        dir_entries = []

    for entry in sorted(dir_entries):
        entry_path = join_path(normalized, entry)
        if should_exclude(entry, entry_path):
            continue
        if entry in RECURSIVE_SKIP_DIRECTORY_NAMES:
            continue
        child = _build_directory_tree(
            workspace, entry_path, current_depth + 1, max_depth, exclude_patterns
        )
        entries.append(child)

    return {"name": name, "type": "dir", "children": entries}


def handle_directory_tree(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Return a nested JSON directory tree for a workspace path."""
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Directory tree")
    path = required_string_param(params, "path")
    max_depth = _int_opt_param(params, "max_depth")
    exclude_patterns = params.get("exclude_patterns")
    if exclude_patterns and isinstance(exclude_patterns, list):
        exclude_patterns = [str(p) for p in exclude_patterns]
    else:
        exclude_patterns = None

    try:
        tree = _build_directory_tree(workspace, path, 0, max_depth, exclude_patterns)
    except Exception as exc:
        raise ToolError(f"Failed to build directory tree for '{path}': {exc}") from exc

    return ToolResult(content=[ToolContent.text_content(_tool_json(tree))], is_error=False)


def handle_search_files(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Search for files matching a glob pattern within a workspace directory."""
    require_capability(session, WORKSPACE_READ_CAPABILITY, "File search")
    pattern = required_string_param(params, "pattern")
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)

    exclude_param = params.get("exclude")
    exclude = (
        [str(p) for p in exclude_param]
        if exclude_param and isinstance(exclude_param, list)
        else None
    )
    limit = _int_param(params, "limit", _GREP_DEFAULT_LIMIT)

    matches = _collect_matching_files(workspace, normalized, pattern, exclude=exclude)
    truncated = len(matches) > limit
    if truncated:
        matches = matches[:limit]

    output = {
        "pattern": pattern,
        "base": path,
        "matches": matches,
        "truncated": truncated,
    }
    return ToolResult(content=[ToolContent.text_content(_tool_json(output))], is_error=False)
