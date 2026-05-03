"""Workspace tool handlers for MCP interactions.

Ports the Rust ``mcp_server::tool_workspace`` helpers into Python so MCP
handlers can read, list, search, and write workspace files while enforcing
session capabilities and edit area policies.
"""

from __future__ import annotations

import base64
import difflib
import fnmatch
import json
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    CoordinationSessionLike,
    ImageContent,
    InvalidParamsError,
    ToolContent,
    ToolError,
    ToolResult,
    require_capability,
)
from ralph.workspace.skip import RECURSIVE_SKIP_DIRECTORY_NAMES

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.workspace import Workspace

WORKSPACE_READ_CAPABILITY = "WorkspaceRead"
WORKSPACE_WRITE_TRACKED_CAPABILITY = "WorkspaceWriteTracked"
WORKSPACE_WRITE_EPHEMERAL_CAPABILITY = "WorkspaceWriteEphemeral"
WORKSPACE_METADATA_READ_CAPABILITY = "WorkspaceMetadataRead"
WORKSPACE_EDIT_CAPABILITY = "WorkspaceEdit"
WORKSPACE_DELETE_CAPABILITY = "WorkspaceDelete"
MEDIA_READ_CAPABILITY = "media.read"

_SUPPORTED_IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_GREP_DEFAULT_LIMIT = 1000
_MAX_PATTERN_LENGTH = 1000
# Default ceiling for full-file reads; larger files are truncated via read_lines.
# The JSON envelope only appears when truncated=True OR when an error occurs.
_FULL_READ_DEFAULT_MAX_BYTES = 5_000_000


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


def _tool_json(data: dict[str, object]) -> str:
    """Serialize a result dict to a JSON string for ToolResult content."""
    return json.dumps(data)


def _int_param(params: dict[str, object], name: str, default: int = 0) -> int:
    """Extract an int parameter from params dict with a default."""
    value = params.get(name, default)
    if isinstance(value, int):
        return value
    return int(str(value))


def _int_opt_param(params: dict[str, object], name: str) -> int | None:
    """Extract an optional int parameter from params dict."""
    value = params.get(name)
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(str(value))


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
    try:
        exists = workspace.exists(normalized)
    except ValueError:
        # Path is outside the workspace's allowed roots; treat as untracked.
        return False
    if not exists:
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
    if entry_name in RECURSIVE_SKIP_DIRECTORY_NAMES:
        return False
    return not workspace.exists(_join_path(entry_path, ".git"))


def _append_dir_entry(
    workspace: Workspace, entry_path: str, output: list[str], depth: int
) -> None:
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


def _list_dir_recursive_output(workspace: Workspace, path: str) -> str:
    normalized = _normalize_relative_path(path)
    output_lines: list[str] = [f"Directory (recursive): {path}\n"]
    _walk_directory_recursive(workspace, normalized, output_lines, 0)
    return "".join(output_lines)


def _match_parts_with_doublestar(
    path_parts: list[str], pat_parts: list[str]
) -> bool:
    """Recursively match path segments against a pattern with ** segments."""
    if not pat_parts:
        return not path_parts
    if pat_parts[0] == "**":
        remaining = pat_parts[1:]
        if not remaining:
            return True
        for i in range(len(path_parts) + 1):
            if _match_parts_with_doublestar(path_parts[i:], remaining):
                return True
        return False
    if not path_parts:
        return False
    return (
        fnmatch.fnmatchcase(path_parts[0], pat_parts[0])
        and _match_parts_with_doublestar(path_parts[1:], pat_parts[1:])
    )


def _match_glob(rel_path: str, pattern: str) -> bool:
    """Match a path against a glob pattern supporting *, **, and ? segments."""
    path_parts = rel_path.split("/")
    pat_parts = pattern.split("/")
    if "**" in pat_parts:
        return _match_parts_with_doublestar(path_parts, pat_parts)
    if len(pat_parts) == 1:
        return any(fnmatch.fnmatchcase(seg, pattern) for seg in path_parts)
    if len(path_parts) < len(pat_parts):
        return False
    tail = path_parts[-len(pat_parts):]
    return all(fnmatch.fnmatchcase(p, q) for p, q in zip(tail, pat_parts, strict=False))


def _collect_files_recursive(workspace: Workspace, base_path: str) -> list[str]:
    """Recursively collect all files under base_path, respecting skip dirs."""
    results: list[str] = []
    entries = _list_dir_entries(workspace, base_path)
    for entry in sorted(entries):
        entry_path = _join_path(base_path, entry)
        if workspace.is_dir(entry_path):
            if _should_recurse_into_directory(workspace, entry_path):
                results.extend(_collect_files_recursive(workspace, entry_path))
        elif workspace.is_file(entry_path):
            results.append(entry_path)
    return results


def _collect_matching_files(
    workspace: Workspace,
    base_path: str,
    pattern: str,
    exclude: list[str] | None = None,
) -> list[str]:
    """Collect files matching a glob pattern under base_path."""
    try:
        all_files: list[str] = list(workspace.iter_files(base_path))
    except Exception:
        all_files = _collect_files_recursive(workspace, base_path)

    matches: list[str] = []
    for file_path in all_files:
        if not _match_glob(file_path, pattern):
            continue
        if exclude and any(_match_glob(file_path, ex) for ex in exclude):
            continue
        matches.append(file_path)

    return sorted(matches)


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
    normalized = _normalize_relative_path(path)

    line_start = params.get("line_start")
    line_end = params.get("line_end")
    offset = params.get("offset")
    limit = params.get("limit")
    head = params.get("head")
    tail = params.get("tail")

    if any(p is not None for p in (line_start, line_end, offset, limit, head, tail)):
        start = _int_opt_param(params, "line_start")
        end = _int_opt_param(params, "line_end")
        off = _int_opt_param(params, "offset")
        lim = _int_opt_param(params, "limit")
        h = _int_opt_param(params, "head")
        t = _int_opt_param(params, "tail")

        if (start is not None or end is not None) and (
            off is not None or lim is not None
        ):
            raise InvalidParamsError(
                "Cannot combine line_start/line_end with offset/limit"
            )
        if (off is not None or lim is not None) and (h is not None or t is not None):
            raise InvalidParamsError("Cannot combine offset/limit with head/tail")
        if (start is not None or end is not None) and (
            h is not None or t is not None
        ):
            raise InvalidParamsError(
                "Cannot combine line_start/line_end with head/tail"
            )

        if off is not None or lim is not None:
            byte_offset = off if off is not None else 0
            content, meta = workspace.read_bytes(normalized, offset=byte_offset, limit=lim)
            payload = {
                "path": path,
                "content": content,
                "total_bytes": meta.get("total_bytes"),
                "returned_bytes": meta.get("returned_bytes"),
                "truncated": meta.get("truncated"),
            }
        else:
            content, meta = workspace.read_lines(
                normalized, start=start, end=end, head=h, tail=t
            )
            payload = {
                "path": path,
                "content": content,
                "total_lines": meta.get("total_lines"),
                "returned_lines": meta.get("returned_lines"),
                "truncated": meta.get("truncated"),
            }
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(payload))], is_error=False
        )

    # Full-file read: check size before reading to enforce max_bytes ceiling.
    max_bytes = _int_param(params, "max_bytes", _FULL_READ_DEFAULT_MAX_BYTES)
    try:
        stat_result: dict[str, object] = workspace.stat(normalized)
    except Exception:
        stat_result = {}

    file_type = stat_result.get("type", "")
    size_bytes = stat_result.get("size_bytes")

    if file_type == "file" and isinstance(size_bytes, int) and size_bytes > max_bytes:
        # File exceeds ceiling: return a representative head with truncation metadata.
        # 256 is an average-line-length heuristic; read_lines enforces its own limits.
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
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(payload))], is_error=False
        )

    try:
        content = workspace.read(normalized)
    except UnicodeDecodeError as exc:
        payload = {
            "status": "binary_or_invalid_utf8",
            "path": path,
            "error": str(exc),
            "byte_offset": exc.start,
        }
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(payload))], is_error=True
        )
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
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Read multiple files")
    paths_param = params.get("paths")
    if not isinstance(paths_param, list):
        raise InvalidParamsError("Missing 'paths' parameter as list of strings")
    paths = [str(p) for p in paths_param]

    results: list[dict[str, object]] = []
    for p in paths:
        normalized = _normalize_relative_path(p)
        try:
            content = workspace.read(normalized)
            results.append({"path": p, "content": content})
        except Exception as exc:
            results.append({"path": p, "error": str(exc)})

    payload = _tool_json({"files": results})
    return ToolResult(
        content=[ToolContent.text_content(payload)], is_error=False
    )


def handle_stat(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    require_capability(
        session, WORKSPACE_METADATA_READ_CAPABILITY, "Workspace metadata read"
    )
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)
    try:
        stat_result = workspace.stat(normalized)
    except Exception as exc:
        raise ToolError(f"Failed to stat '{path}': {exc}") from exc
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(stat_result))], is_error=False
    )


def handle_list_allowed_roots(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
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
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Directory listing")
    path = required_string_param(params, "path")
    recursive = bool(params.get("recursive", False))
    output = (
        _list_dir_flat(workspace, path)
        if not recursive
        else _list_dir_recursive_output(workspace, path)
    )
    return ToolResult(content=[ToolContent.text_content(output)], is_error=False)


def handle_list_directory_recursive(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    require_capability(
        session, WORKSPACE_READ_CAPABILITY, "Recursive directory listing"
    )
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
    normalized = _normalize_relative_path(path)
    name = normalized.split("/")[-1] if normalized else path

    def should_exclude(entry_name: str, entry_path: str) -> bool:
        if not exclude_patterns:
            return False
        for pat in exclude_patterns:
            if fnmatch.fnmatchcase(entry_name, pat) or fnmatch.fnmatchcase(
                entry_path, pat
            ):
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
        entry_path = _join_path(normalized, entry)
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
        raise ToolError(
            f"Failed to build directory tree for '{path}': {exc}"
        ) from exc

    return ToolResult(
        content=[ToolContent.text_content(_tool_json(tree))], is_error=False
    )


def handle_search_files(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    require_capability(session, WORKSPACE_READ_CAPABILITY, "File search")
    pattern = required_string_param(params, "pattern")
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)

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
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(output))], is_error=False
    )


def _compile_grep_pattern(
    pattern: str,
    *,
    is_regex: bool,
    case_sensitive: bool,
    whole_word: bool,
) -> re.Pattern[str]:
    """Compile a grep search pattern to a regex."""
    flags = 0 if case_sensitive else re.IGNORECASE
    if is_regex:
        try:
            return re.compile(pattern, flags)
        except re.error as exc:
            raise InvalidParamsError(f"Invalid regex pattern: {exc}") from exc
    escaped = re.escape(pattern)
    if whole_word:
        escaped = r"\b" + escaped + r"\b"
    return re.compile(escaped, flags)


def _collect_files_for_grep(workspace: Workspace, normalized: str) -> list[str]:
    """Collect all files under normalized path for grep, with fallback."""
    try:
        return list(workspace.iter_files(normalized))
    except Exception:
        return _collect_files_recursive(workspace, normalized)


def _search_file_content(  # noqa: PLR0913
    workspace: Workspace,
    file_path: str,
    compiled: re.Pattern[str],
    context_before: int,
    context_after: int,
    max_file_bytes: int,
) -> list[dict[str, object]] | None:
    """Search a single file for matches; returns None if the file should be skipped."""
    try:
        file_stat = workspace.stat(file_path)
    except Exception:
        return None

    if file_stat.get("type") == "dir":
        return None
    size_bytes = file_stat.get("size_bytes", 0)
    if isinstance(size_bytes, int) and size_bytes > max_file_bytes:
        return None

    try:
        content = workspace.read(file_path)
    except (UnicodeDecodeError, Exception):
        return None

    lines = content.splitlines(keepends=True)
    matches: list[dict[str, object]] = []
    for line_no, line in enumerate(lines, 1):
        if not compiled.search(line):
            continue
        start_idx = max(0, line_no - 1 - context_before)
        ctx_before = [
            lines[i].rstrip("\n\r") for i in range(start_idx, line_no - 1)
        ]
        end_idx = min(len(lines), line_no + context_after)
        ctx_after = [lines[i].rstrip("\n\r") for i in range(line_no, end_idx)]
        matches.append({
            "path": file_path,
            "line": line_no,
            "text": line.rstrip("\n\r"),
            "context_before": ctx_before,
            "context_after": ctx_after,
        })
    return matches


def handle_grep_files(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Content search")
    pattern = required_string_param(params, "pattern")
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)

    is_regex = bool(params.get("regex", True))
    case_sensitive = bool(params.get("case_sensitive", True))
    whole_word = bool(params.get("whole_word", False))
    include_param = params.get("include")
    include = (
        [str(p) for p in include_param]
        if include_param and isinstance(include_param, list)
        else None
    )
    exclude_param = params.get("exclude")
    exclude = (
        [str(p) for p in exclude_param]
        if exclude_param and isinstance(exclude_param, list)
        else None
    )
    context_before = _int_param(params, "context_before", 0)
    context_after = _int_param(params, "context_after", 0)
    limit = _int_param(params, "limit", _GREP_DEFAULT_LIMIT)
    max_file_bytes = _int_param(params, "max_file_bytes", 5_000_000)

    if len(pattern) > _MAX_PATTERN_LENGTH:
        raise InvalidParamsError(
            f"Pattern exceeds maximum length of {_MAX_PATTERN_LENGTH} characters"
        )

    compiled = _compile_grep_pattern(
        pattern,
        is_regex=is_regex,
        case_sensitive=case_sensitive,
        whole_word=whole_word,
    )

    all_files = _collect_files_for_grep(workspace, normalized)

    matches: list[dict[str, object]] = []
    skipped_files = 0
    truncated = False

    for file_path in all_files:
        if include and not any(_match_glob(file_path, p) for p in include):
            continue
        if exclude and any(_match_glob(file_path, p) for p in exclude):
            continue

        file_matches = _search_file_content(
            workspace,
            file_path,
            compiled,
            context_before,
            context_after,
            max_file_bytes,
        )
        if file_matches is None:
            skipped_files += 1
            continue

        for m in file_matches:
            matches.append(m)
            if len(matches) >= limit:
                truncated = True
                break

        if truncated:
            break

    result = {
        "pattern": pattern,
        "base": path,
        "matches": matches,
        "truncated": truncated,
        "skipped_files": skipped_files,
    }
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(result))], is_error=False
    )


def handle_write_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    # Path is extracted first to determine which write capability applies
    # (tracked vs ephemeral), then capability check fires before content extraction.
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)
    _check_edit_area_restriction(session, normalized)
    is_tracked = _is_path_git_tracked(workspace, normalized)
    capability = (
        WORKSPACE_WRITE_TRACKED_CAPABILITY
        if is_tracked
        else WORKSPACE_WRITE_EPHEMERAL_CAPABILITY
    )
    require_capability(session, capability, "Workspace write")
    content = required_string_param(params, "content")
    _write_file_to_workspace(workspace, normalized, content)
    return ToolResult(
        content=[ToolContent.text_content(
            f"Successfully wrote {len(content)} bytes to {path}"
        )],
        is_error=False,
    )


def handle_edit_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)
    _check_edit_area_restriction(session, normalized)
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
                content=[ToolContent.text_content(_tool_json({
                    "status": "no_match",
                    "edit_index": i,
                    "preview": "".join(diff),
                }))],
                is_error=True,
            )
        current_content = (
            current_content[:idx] + new_text + current_content[idx + len(old_text):]
        )
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
            content=[ToolContent.text_content(_tool_json({
                "status": "preview",
                "diff": "".join(diff),
                "edits_applied": len(applied_edits),
            }))],
            is_error=False,
        )

    try:
        workspace.write(normalized, current_content)
    except Exception as exc:
        raise ToolError(f"Failed to write file '{path}': {exc}") from exc

    return ToolResult(
        content=[ToolContent.text_content(_tool_json({
            "status": "applied",
            "diff": "".join(diff),
            "bytes_written": len(current_content),
        }))],
        is_error=False,
    )


def handle_append_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)
    _check_edit_area_restriction(session, normalized)
    require_capability(session, WORKSPACE_EDIT_CAPABILITY, "Workspace append")
    content = required_string_param(params, "content")

    try:
        workspace.append(normalized, content)
    except Exception as exc:
        raise ToolError(f"Failed to append to file '{path}': {exc}") from exc

    return ToolResult(
        content=[ToolContent.text_content(_tool_json({
            "path": path,
            "bytes_appended": len(content),
        }))],
        is_error=False,
    )


def handle_create_directory(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)
    _check_edit_area_restriction(session, normalized)
    require_capability(session, WORKSPACE_EDIT_CAPABILITY, "Create directory")

    try:
        workspace.mkdirs(normalized)
    except Exception as exc:
        raise ToolError(f"Failed to create directory '{path}': {exc}") from exc

    return ToolResult(
        content=[ToolContent.text_content(_tool_json({
            "path": path,
            "created": True,
        }))],
        is_error=False,
    )


def handle_move_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    src = required_string_param(params, "src")
    dest = required_string_param(params, "dest")
    src_norm = _normalize_relative_path(src)
    dest_norm = _normalize_relative_path(dest)
    _check_edit_area_restriction(session, src_norm)
    _check_edit_area_restriction(session, dest_norm)
    require_capability(session, WORKSPACE_EDIT_CAPABILITY, "Move file")
    overwrite = bool(params.get("overwrite", False))

    try:
        workspace.move(src_norm, dest_norm, overwrite=overwrite)
    except FileExistsError:
        raise ToolError(f"Destination '{dest}' already exists") from None
    except Exception as exc:
        raise ToolError(f"Failed to move '{src}' to '{dest}': {exc}") from exc

    return ToolResult(
        content=[ToolContent.text_content(_tool_json({
            "src": src,
            "dest": dest,
        }))],
        is_error=False,
    )


def handle_copy_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    src = required_string_param(params, "src")
    dest = required_string_param(params, "dest")
    src_norm = _normalize_relative_path(src)
    dest_norm = _normalize_relative_path(dest)
    _check_edit_area_restriction(session, dest_norm)
    require_capability(session, WORKSPACE_EDIT_CAPABILITY, "Copy file")
    overwrite = bool(params.get("overwrite", False))

    try:
        workspace.copy(src_norm, dest_norm, overwrite=overwrite)
    except FileExistsError:
        raise ToolError(f"Destination '{dest}' already exists") from None
    except Exception as exc:
        raise ToolError(f"Failed to copy '{src}' to '{dest}': {exc}") from exc

    return ToolResult(
        content=[ToolContent.text_content(_tool_json({
            "src": src,
            "dest": dest,
        }))],
        is_error=False,
    )


def handle_delete_path(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)
    _check_edit_area_restriction(session, normalized)
    require_capability(session, WORKSPACE_DELETE_CAPABILITY, "Delete path")
    recursive = bool(params.get("recursive", False))

    try:
        workspace.delete(normalized, recursive=recursive)
    except IsADirectoryError:
        return ToolResult(
            content=[ToolContent.text_content(
                f"Path '{path}' is a directory, use recursive=True to delete"
            )],
            is_error=True,
        )
    except FileNotFoundError:
        raise ToolError(f"Path '{path}' not found") from None
    except Exception as exc:
        raise ToolError(f"Failed to delete '{path}': {exc}") from exc

    return ToolResult(
        content=[ToolContent.text_content(_tool_json({
            "path": path,
            "deleted": True,
            "recursive": recursive,
        }))],
        is_error=False,
    )


def _infer_image_mime_type(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix.lower()
    return _SUPPORTED_IMAGE_MIME_TYPES.get(suffix)


def handle_read_image(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
    *,
    max_inline_bytes: int = 5_242_880,
) -> ToolResult:
    """Read an image file and return it as a base64-encoded content block.

    Requires MediaRead capability. Enforces size limit and supported MIME types.
    """
    require_capability(session, MEDIA_READ_CAPABILITY, "Image read")
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)

    mime_type = _infer_image_mime_type(normalized or path)
    if mime_type is None:
        suffix = PurePosixPath(path).suffix.lower() or "(none)"
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Unsupported image format '{suffix}'. "
                    f"Supported: {', '.join(sorted(_SUPPORTED_IMAGE_MIME_TYPES.keys()))}"
                )
            ],
            is_error=True,
        )

    abs_path = workspace.absolute_path(normalized or path)
    try:
        file_size = Path(abs_path).stat().st_size
    except OSError as exc:
        return ToolResult(
            content=[ToolContent.text_content(
                f"Failed to stat image file '{path}': {exc}"
            )],
            is_error=True,
        )

    if file_size > max_inline_bytes:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Image file '{path}' is too large ({file_size} bytes). "
                    f"Maximum allowed: {max_inline_bytes} bytes."
                )
            ],
            is_error=True,
        )

    try:
        with Path(abs_path).open("rb") as fh:
            raw_bytes = fh.read()
    except OSError as exc:
        return ToolResult(
            content=[ToolContent.text_content(
                f"Failed to read image file '{path}': {exc}"
            )],
            is_error=True,
        )

    encoded = base64.b64encode(raw_bytes).decode("ascii")
    return ToolResult(
        content=[ImageContent(data=encoded, mime_type=mime_type)],
        is_error=False,
    )


__all__ = [
    "MEDIA_READ_CAPABILITY",
    "WORKSPACE_DELETE_CAPABILITY",
    "WORKSPACE_EDIT_CAPABILITY",
    "WORKSPACE_METADATA_READ_CAPABILITY",
    "WORKSPACE_READ_CAPABILITY",
    "WORKSPACE_WRITE_EPHEMERAL_CAPABILITY",
    "WORKSPACE_WRITE_TRACKED_CAPABILITY",
    "handle_append_file",
    "handle_copy_file",
    "handle_create_directory",
    "handle_delete_path",
    "handle_directory_tree",
    "handle_edit_file",
    "handle_grep_files",
    "handle_list_allowed_roots",
    "handle_list_directory",
    "handle_list_directory_recursive",
    "handle_move_file",
    "handle_read_file",
    "handle_read_image",
    "handle_read_multiple_files",
    "handle_search_files",
    "handle_stat",
    "handle_write_file",
    "required_string_param",
]
