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
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, NamedTuple, cast

from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.multimodal.artifacts import (
    INLINE_IMAGE_MIME_TYPES,
    AudioContent,
    DocumentContent,
    PdfContent,
    ResourceReferenceContent,
    VideoContent,
    infer_modality_and_mime,
)
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    DeliveryMode,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    resolve_capability_profile,
)
from ralph.mcp.multimodal.errors import MultimodalFailureKind
from ralph.mcp.multimodal.resources import (
    MediaManifest,
    build_media_identity,
    parse_media_uri,
)
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
from ralph.prompts.debug_dump import (
    media_cache_artifact_path,
    media_registry_path,
    media_session_path,
)
from ralph.workspace.skip import RECURSIVE_SKIP_DIRECTORY_NAMES

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.multimodal.capabilities import CapabilityVerdict
    from ralph.mcp.multimodal.resources import ManifestEntry
    from ralph.mcp.tools.coordination import ContentBlock
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


def _list_dir_recursive_output(workspace: Workspace, path: str) -> str:
    normalized = _normalize_relative_path(path)
    output_lines: list[str] = [f"Directory (recursive): {path}\n"]
    _walk_directory_recursive(workspace, normalized, output_lines, 0)
    return "".join(output_lines)


def _match_parts_with_doublestar(path_parts: list[str], pat_parts: list[str]) -> bool:
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
    return fnmatch.fnmatchcase(path_parts[0], pat_parts[0]) and _match_parts_with_doublestar(
        path_parts[1:], pat_parts[1:]
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
    tail = path_parts[-len(pat_parts) :]
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


class _ReadSelector(NamedTuple):
    """Normalized partial-read selectors for read_file."""

    start: int | None  # line_start (1-based)
    end: int | None  # line_end (1-based)
    off: int | None  # byte offset
    lim: int | None  # byte limit
    head: int | None  # first N lines
    tail: int | None  # last N lines

    @classmethod
    def from_params(cls, params: dict[str, object]) -> _ReadSelector:
        """Extract and normalize selectors from raw MCP params.

        Treats 0 as absent for all params except offset (offset=0 is a valid
        start-of-file position). Inert zero defaults sent by brokers are
        normalized to None so they do not trigger mode selection.
        """

        def _n(v: int | None) -> int | None:
            return None if v == 0 else v

        return cls(
            start=_n(_int_opt_param(params, "line_start")),
            end=_n(_int_opt_param(params, "line_end")),
            off=_int_opt_param(params, "offset"),
            lim=_n(_int_opt_param(params, "limit")),
            head=_n(_int_opt_param(params, "head")),
            tail=_n(_int_opt_param(params, "tail")),
        )

    def is_active(self) -> bool:
        """Return True when at least one partial-read mode is requested."""
        line_range = (self.start is not None) or (self.end is not None)
        byte_window = (self.off is not None and self.off > 0) or (self.lim is not None)
        return line_range or byte_window or (self.head is not None) or (self.tail is not None)


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
    normalized = _normalize_relative_path(path)

    sel = _ReadSelector.from_params(params)
    if sel.is_active():
        return _dispatch_partial_read(workspace, normalized, path, sel)

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
        normalized = _normalize_relative_path(p)
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
    normalized = _normalize_relative_path(path)
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
    normalized = _normalize_relative_path(path)
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
    return ToolResult(content=[ToolContent.text_content(_tool_json(output))], is_error=False)


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


def _search_file_content(
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
        ctx_before = [lines[i].rstrip("\n\r") for i in range(start_idx, line_no - 1)]
        end_idx = min(len(lines), line_no + context_after)
        ctx_after = [lines[i].rstrip("\n\r") for i in range(line_no, end_idx)]
        matches.append(
            {
                "path": file_path,
                "line": line_no,
                "text": line.rstrip("\n\r"),
                "context_before": ctx_before,
                "context_after": ctx_after,
            }
        )
    return matches


def handle_grep_files(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Search file contents for a pattern and return line-level matches."""
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
    return ToolResult(content=[ToolContent.text_content(_tool_json(result))], is_error=False)


def handle_write_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Write UTF-8 content to a workspace file, creating it if necessary."""
    # Path is extracted first to determine which write capability applies
    # (tracked vs ephemeral), then capability check fires before content extraction.
    path = required_string_param(params, "path")
    normalized = _normalize_relative_path(path)
    _check_edit_area_restriction(session, normalized)
    is_tracked = _is_path_git_tracked(workspace, normalized)
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
    normalized = _normalize_relative_path(path)
    _check_edit_area_restriction(session, normalized)
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
    normalized = _normalize_relative_path(path)
    _check_edit_area_restriction(session, normalized)
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
    normalized = _normalize_relative_path(path)
    _check_edit_area_restriction(session, normalized)
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


def _infer_image_mime_type(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix.lower()
    return _SUPPORTED_IMAGE_MIME_TYPES.get(suffix)


def _get_media_manifest(session: object) -> MediaManifest | None:
    """Return the session's MediaManifest if available."""
    raw: object = getattr(session, "media_manifest", None)
    if isinstance(raw, MediaManifest):
        return raw
    return None


def _get_session_model_identity(session: object) -> MultimodalModelIdentity:
    """Extract the model identity from a session, defaulting to UNKNOWN_IDENTITY."""
    raw: object = getattr(session, "model_identity", None)
    if isinstance(raw, MultimodalModelIdentity):
        return raw
    return UNKNOWN_IDENTITY


def _get_session_capability_profile(session: object) -> ResolvedCapabilityProfile:
    """Return the resolved capability profile from a session.

    Prefers a pre-resolved profile from the session (populated by the managed
    runtime path from the persisted session contract), falling back to
    computing one from the session's model identity.
    """
    raw: object = getattr(session, "capability_profile", None)
    if isinstance(raw, ResolvedCapabilityProfile):
        return raw
    return resolve_capability_profile(_get_session_model_identity(session))


_MEDIA_SESSION_SCHEMA_VERSION = "2"


def _workspace_artifact_loader(
    workspace: Workspace,
    cache_path: str,
    source_path: str,
) -> Callable[[], bytes | None]:
    """Build a lazy artifact loader bound to a workspace replay source."""

    def _loader() -> bytes | None:
        return _load_artifact_bytes(workspace, cache_path, source_path)

    return _loader


def _media_session_identity(entry: dict[str, str]) -> str:
    """Return the dedupe identity for a persisted media-session entry."""
    identity_key = entry.get("identity_key", "")
    if identity_key:
        return identity_key
    source_uri = entry.get("source_uri", "")
    source_path = entry.get("source_path", "")
    modality = entry.get("modality", "")
    artifact_id = entry.get("artifact_id", "")
    uri = entry.get("uri", "")
    if source_uri:
        return f"source-uri:{modality}:{source_uri}"
    if source_path:
        return f"source-path:{modality}:{source_path}"
    return f"artifact-id:{artifact_id or uri}"


def _write_durable_media_cache(
    workspace: Workspace,
    artifact_id: str,
    raw_bytes: bytes,
) -> str:
    """Write raw bytes to the durable media cache and return the workspace-relative path."""
    cache_path = media_cache_artifact_path(artifact_id)
    try:
        abs_path = Path(workspace.absolute_path(cache_path))
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(raw_bytes)
    except Exception:
        return ""
    return cache_path


def _persist_media_registry_entry(
    workspace: Workspace,
    entry: dict[str, str],
) -> None:
    """Write entry to the centralized media registry for cross-session lookup."""
    path = media_registry_path()
    artifact_id = entry["artifact_id"]
    try:
        artifacts: list[dict[str, str]] = []
        try:
            data: dict[str, object] = json.loads(workspace.read(path))
            raw_artifacts = data.get("artifacts", [])
            artifacts = list(raw_artifacts) if isinstance(raw_artifacts, list) else []
        except Exception:
            artifacts = []
        artifacts = [a for a in artifacts if a.get("artifact_id") != artifact_id]
        artifacts.append(entry)
        payload: dict[str, object] = {
            "schema_version": _MEDIA_SESSION_SCHEMA_VERSION,
            "artifacts": artifacts,
        }
        workspace.write(path, json.dumps(payload, indent=2))
    except Exception:
        pass


def _load_persisted_registry_entry(
    workspace: Workspace,
    artifact_id: str,
) -> dict[str, str] | None:
    """Look up a persisted media artifact entry from the centralized registry."""
    path = media_registry_path()
    try:
        data: dict[str, object] = json.loads(workspace.read(path))
        raw_artifacts = data.get("artifacts", [])
        artifacts: list[dict[str, str]] = (
            list(raw_artifacts) if isinstance(raw_artifacts, list) else []
        )
        for entry in artifacts:
            if entry.get("artifact_id") == artifact_id:
                return entry
    except Exception:
        pass
    return None


def _load_artifact_bytes(
    workspace: Workspace,
    cache_path: str,
    source_path: str,
) -> bytes | None:
    """Load artifact bytes from cache_path (durable cache) or source_path (original file)."""
    if cache_path:
        try:
            return Path(workspace.absolute_path(cache_path)).read_bytes()
        except Exception:
            pass
    if source_path:
        try:
            return Path(workspace.absolute_path(source_path)).read_bytes()
        except Exception:
            pass
    return None


def _persist_media_session_entry(
    session: object,
    workspace: Workspace,
    meta: dict[str, str],
) -> None:
    """Upsert a resource-reference artifact into the persistent session media index."""
    drain: object = getattr(session, "drain", None)
    phase = str(drain) if drain else "standalone"
    path = media_session_path(phase)
    uri = meta["uri"]
    artifact_id = uri.rsplit("/", maxsplit=1)[-1]
    new_entry: dict[str, str] = {
        "artifact_id": artifact_id,
        "uri": uri,
        "mime_type": meta["mime_type"],
        "title": meta["title"],
        "modality": meta["modality"],
        "delivery": meta.get("delivery", "resource_reference_replay"),
        "reason": meta["reason"],
        "source_path": meta.get("source_path", ""),
        "cache_path": meta.get("cache_path", ""),
        "source_uri": meta.get("source_uri", ""),
        "block_type": meta.get("block_type", ""),
        "failure_kind": meta.get("failure_kind", ""),
        "identity_key": meta.get("identity_key", ""),
    }
    try:
        try:
            data: dict[str, object] = json.loads(workspace.read(path))
            raw_artifacts = data.get("artifacts", [])
            artifacts: list[dict[str, str]] = (
                list(raw_artifacts) if isinstance(raw_artifacts, list) else []
            )
        except Exception:
            artifacts = []

        new_identity = _media_session_identity(new_entry)
        ordered: OrderedDict[str, dict[str, str]] = OrderedDict()
        for artifact in artifacts:
            normalized = {str(k): str(v) for k, v in artifact.items()}
            ordered[_media_session_identity(normalized)] = normalized
        ordered[new_identity] = new_entry
        payload: dict[str, object] = {
            "schema_version": _MEDIA_SESSION_SCHEMA_VERSION,
            "phase": phase,
            "artifacts": list(ordered.values()),
        }
        workspace.write(path, json.dumps(payload, indent=2))
    except Exception:
        pass  # Session index persistence is best-effort; never block a tool call
    _persist_media_registry_entry(workspace, new_entry)


def _make_typed_block(
    block_type: str,
    *,
    uri: str,
    mime_type: str,
    title: str,
) -> PdfContent | DocumentContent | AudioContent | VideoContent | None:
    """Build the correct typed content block for a TYPED_BLOCK verdict."""
    if block_type == "pdf":
        return PdfContent(uri=uri, mime_type=mime_type, title=title)
    if block_type == "document":
        return DocumentContent(uri=uri, mime_type=mime_type, title=title)
    if block_type == "audio":
        return AudioContent(uri=uri, mime_type=mime_type, title=title)
    if block_type == "video":
        return VideoContent(uri=uri, mime_type=mime_type, title=title)
    return None


def _make_non_inline_workspace_block(
    verdict: CapabilityVerdict,
    entry: ManifestEntry,
    mime_type: str,
    modality: str,
    title: str,
) -> tuple[ContentBlock, DeliveryMode]:
    """Return (content_block, delivery_mode) for non-inline workspace delivery."""
    if verdict.delivery == DeliveryMode.TYPED_BLOCK and verdict.block_type:
        block = _make_typed_block(
            verdict.block_type,
            uri=entry.uri,
            mime_type=mime_type,
            title=title,
        )
        if block is not None:
            return block, DeliveryMode.TYPED_BLOCK
    ref = ResourceReferenceContent(
        uri=entry.uri,
        mime_type=mime_type,
        title=title,
        modality=modality,
        delivery=DeliveryMode.RESOURCE_REFERENCE_REPLAY,
    )
    return ref, DeliveryMode.RESOURCE_REFERENCE_REPLAY


def _replay_from_manifest_entry(
    session: CoordinationSessionLike,
    entry: ManifestEntry,
) -> ToolResult:
    """Return the appropriate typed block from a live manifest entry."""
    profile = _get_session_capability_profile(session)
    verdict = profile.verdict_for(entry.modality)
    raw_bytes = entry.load_bytes()
    if verdict.delivery == DeliveryMode.INLINE_IMAGE:
        if raw_bytes is None:
            return ToolResult(
                content=[
                    ToolContent.text_content(
                        f"{MultimodalFailureKind.MISSING_REPLAY_SOURCE}: "
                        f"Artifact '{entry.uri}' is no longer available from its replay source."
                    )
                ],
                is_error=True,
            )
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return ToolResult(
            content=[ImageContent(data=encoded, mime_type=entry.mime_type)],
            is_error=False,
        )
    if verdict.delivery == DeliveryMode.TYPED_BLOCK and verdict.block_type:
        block = _make_typed_block(
            verdict.block_type,
            uri=entry.uri,
            mime_type=entry.mime_type,
            title=entry.title,
        )
        if block is not None:
            return ToolResult(content=[block], is_error=False)
    if verdict.delivery == DeliveryMode.UNSUPPORTED:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Modality '{entry.modality}' is not supported by provider "
                    f"'{verdict.provider}' (model: {verdict.model_id or 'unknown'}). "
                    f"Reason: {verdict.reason}"
                )
            ],
            is_error=True,
        )
    ref = ResourceReferenceContent(
        uri=entry.uri,
        mime_type=entry.mime_type,
        title=entry.title,
        modality=entry.modality,
        delivery=verdict.delivery,
    )
    return ToolResult(content=[ref], is_error=False)


def _replay_from_persisted_entry(
    session: CoordinationSessionLike,
    workspace: Workspace,
    persisted: dict[str, str],
    original_path: str,
) -> ToolResult:
    """Replay a media artifact from persisted v2 registry metadata."""
    cache_path = persisted.get("cache_path", "")
    source_path = persisted.get("source_path", "")
    modality = persisted.get("modality", "")
    mime_type = persisted.get("mime_type", "")
    title = persisted.get("title", "")
    block_type = persisted.get("block_type", "")
    uri = persisted.get("uri", original_path)

    raw_bytes = _load_artifact_bytes(workspace, cache_path, source_path)
    if raw_bytes is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"{MultimodalFailureKind.MISSING_REPLAY_SOURCE}: "
                    f"Artifact '{original_path}' was found in the registry but its "
                    f"cached bytes are no longer available "
                    f"(cache_path={cache_path!r}, source_path={source_path!r}). "
                    f"The original source may have been modified or removed."
                )
            ],
            is_error=True,
        )

    profile = _get_session_capability_profile(session)
    verdict = profile.verdict_for(modality)
    if verdict.delivery == DeliveryMode.INLINE_IMAGE:
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return ToolResult(
            content=[ImageContent(data=encoded, mime_type=mime_type)],
            is_error=False,
        )
    if verdict.delivery == DeliveryMode.TYPED_BLOCK and block_type:
        block = _make_typed_block(block_type, uri=uri, mime_type=mime_type, title=title)
        if block is not None:
            return ToolResult(content=[block], is_error=False)
    if verdict.delivery == DeliveryMode.UNSUPPORTED:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Modality '{modality}' is not supported by provider "
                    f"'{verdict.provider}' (model: {verdict.model_id or 'unknown'}). "
                    f"Reason: {verdict.reason}"
                )
            ],
            is_error=True,
        )
    ref = ResourceReferenceContent(
        uri=uri,
        mime_type=mime_type,
        title=title,
        modality=modality,
        delivery=verdict.delivery,
    )
    return ToolResult(content=[ref], is_error=False)


def _handle_replay_uri(
    session: CoordinationSessionLike,
    workspace: Workspace,
    path: str,
) -> ToolResult:
    artifact_id = parse_media_uri(path)
    if artifact_id is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"{MultimodalFailureKind.INVALID_REPLAY_HANDLE}: "
                    f"'{path}' is not a valid ralph://media/{{artifact_id}} handle. "
                    f"Use the URI exactly as returned by a prior read_media call."
                )
            ],
            is_error=True,
        )
    manifest = _get_media_manifest(session)
    entry = manifest.get(artifact_id) if manifest is not None else None
    if entry is not None:
        return _replay_from_manifest_entry(session, entry)
    persisted = _load_persisted_registry_entry(workspace, artifact_id)
    if persisted is not None:
        return _replay_from_persisted_entry(session, workspace, persisted, path)
    return ToolResult(
        content=[
            ToolContent.text_content(
                f"{MultimodalFailureKind.MISSING_REPLAY_SOURCE}: "
                f"Artifact '{path}' is not available in the current session manifest "
                f"or the persisted registry. The artifact may be from an earlier session "
                f"whose cache has been cleared, or it was never created."
            )
        ],
        is_error=True,
    )


def _handle_workspace_media(
    session: CoordinationSessionLike,
    workspace: Workspace,
    path: str,
    max_inline_bytes: int,
) -> ToolResult:
    normalized = _normalize_relative_path(path)
    suffix = PurePosixPath(normalized or path).suffix.lower()
    inferred = infer_modality_and_mime(suffix)
    if inferred is None:
        supported = sorted(
            {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
                ".pdf",
                ".mp3",
                ".wav",
                ".ogg",
                ".m4a",
                ".flac",
                ".aac",
                ".mp4",
                ".avi",
                ".mov",
                ".mkv",
                ".webm",
                ".docx",
                ".pptx",
                ".xlsx",
            }
        )
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Unsupported media format '{suffix or '(none)'}'. "
                    f"Supported: {', '.join(supported)}"
                )
            ],
            is_error=True,
        )
    modality, mime_type = inferred
    profile = _get_session_capability_profile(session)
    verdict = profile.verdict_for(modality)
    if verdict.delivery == DeliveryMode.UNSUPPORTED:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Modality '{modality}' is not supported by provider '{verdict.provider}' "
                    f"(model: {verdict.model_id or 'unknown'}). "
                    f"Accepted forms: typed_block or none. Reason: {verdict.reason}"
                )
            ],
            is_error=True,
        )
    abs_path = workspace.absolute_path(normalized or path)
    try:
        raw_bytes = Path(abs_path).read_bytes()
    except OSError as exc:
        return ToolResult(
            content=[ToolContent.text_content(f"Failed to read media file '{path}': {exc}")],
            is_error=True,
        )
    file_size = len(raw_bytes)
    title = PurePosixPath(path).name
    if (
        verdict.delivery == DeliveryMode.INLINE_IMAGE
        and modality == "image"
        and mime_type in INLINE_IMAGE_MIME_TYPES
        and file_size <= max_inline_bytes
    ):
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return ToolResult(content=[ImageContent(data=encoded, mime_type=mime_type)], is_error=False)
    manifest = _get_media_manifest(session)
    if manifest is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Media file '{path}' ({modality}, {mime_type}) cannot be delivered: "
                    f"no active session manifest is available. "
                    f"Resource-reference delivery requires an active session."
                )
            ],
            is_error=True,
        )
    source_path = normalized or path
    identity_key = build_media_identity(
        modality=modality,
        mime_type=mime_type,
        title=title,
        source_path=source_path,
        raw_bytes=raw_bytes,
    )
    entry = manifest.add(
        title=title,
        mime_type=mime_type,
        modality=modality,
        raw_bytes=raw_bytes,
        source_path=source_path,
        identity_key=identity_key,
    )
    block, delivery = _make_non_inline_workspace_block(verdict, entry, mime_type, modality, title)
    artifact_id = entry.uri.rsplit("/", maxsplit=1)[-1]
    cache_path = _write_durable_media_cache(workspace, artifact_id, raw_bytes)
    entry.set_replay_source(
        cache_path=cache_path,
        source_path=source_path,
        byte_loader=_workspace_artifact_loader(workspace, cache_path, source_path),
    )
    _persist_media_session_entry(
        session,
        workspace,
        {
            "uri": entry.uri,
            "mime_type": mime_type,
            "title": title,
            "modality": modality,
            "delivery": delivery,
            "reason": verdict.reason,
            "source_path": source_path,
            "cache_path": cache_path,
            "source_uri": "",
            "block_type": verdict.block_type or "",
            "identity_key": identity_key,
        },
    )
    return ToolResult(content=[block], is_error=False)


def handle_read_media(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
    *,
    max_inline_bytes: int = 5_242_880,
) -> ToolResult:
    """Read a media file or replay a stored artifact handle.

    Accepts either:
    - a workspace file path (e.g., ``screenshots/shot.png``)
    - a ``ralph://media/{artifact_id}`` replay handle from a prior session

    When given a replay handle, rehydrates the artifact from the live session
    manifest and returns the same typed block that was originally emitted.
    Invalid or unrecognised handles return an explicit structured failure.

    For workspace paths, delivery mode is determined by the session's model
    identity via the capability matrix: INLINE_IMAGE, TYPED_BLOCK,
    RESOURCE_REFERENCE_REPLAY, or UNSUPPORTED.
    """
    require_capability(session, MEDIA_READ_CAPABILITY, "Media read")
    path = required_string_param(params, "path")
    if path.startswith("ralph://media/"):
        return _handle_replay_uri(session, workspace, path)
    return _handle_workspace_media(session, workspace, path, max_inline_bytes)


def handle_read_image(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
    *,
    max_inline_bytes: int = 5_242_880,
) -> ToolResult:
    """Read an image file and return it as a capability-aware content block.

    Requires MediaRead capability. Validates that the file is a supported image
    format, then delegates to the shared workspace media handler for delivery
    decision (inline image, typed block, or explicit unsupported/error).

    This is a compatibility alias over ``_handle_workspace_media`` that restricts
    inputs to image formats only while preserving the same truthful delivery
    contract as ``read_media``.
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

    # Delegate to the shared capability-aware handler for delivery decision.
    # This ensures read_image respects the same INLINE_IMAGE / TYPED_BLOCK /
    # RESOURCE_REFERENCE_REPLAY / UNSUPPORTED contract as read_media.
    return _handle_workspace_media(session, workspace, path, max_inline_bytes)


def _extract_resource_reference_replay_blocks(
    result: object,
) -> list[dict[str, str]]:
    """Extract resource_reference_replay blocks from a normalized upstream result."""
    if not isinstance(result, dict):
        return []
    raw_content: object = result.get("content")
    if not isinstance(raw_content, list):
        return []
    blocks: list[dict[str, str]] = []
    for item in raw_content:
        if not isinstance(item, dict):
            continue
        block: dict[str, str] = {k: str(v) for k, v in item.items() if isinstance(v, str)}
        if (
            block.get("type") == "resource_reference"
            and block.get("delivery") == "resource_reference_replay"
        ):
            blocks.append(block)
    return blocks


def _extract_resource_reference_blocks(
    result: object,
) -> list[dict[str, str]]:
    """Extract URI-backed resource_reference blocks from a normalized upstream result.

    These blocks reference external URIs (not Ralph-owned artifacts) and cannot
    be replayed across sessions. They are synthesized as unsupported_runtime_seam
    entries at the cross-session handoff boundary.
    """
    if not isinstance(result, dict):
        return []
    raw_content: object = result.get("content")
    if not isinstance(raw_content, list):
        return []
    blocks: list[dict[str, str]] = []
    for item in raw_content:
        if not isinstance(item, dict):
            continue
        block: dict[str, str] = {k: str(v) for k, v in item.items() if isinstance(v, str)}
        if (
            block.get("type") == "resource_reference"
            and block.get("delivery") == "resource_reference"
        ):
            blocks.append(block)
    return blocks


def persist_upstream_media_artifacts(
    result: object,
    session: object,
    workspace: Workspace,
) -> None:
    """Persist upstream embedded media artifacts to the durable cache and session index.

    Called after normalize_upstream_content_blocks so that:

    - resource_reference_replay blocks (backed by ralph://media/... URIs stored in
      the session manifest) are written to the durable cache and session index,
      enabling cross-session replay of artifacts from upstream embedded-data blocks.

    - URI-backed resource_reference blocks (delivery='resource_reference') reference
      external URIs and cannot be replayed across sessions. These are synthesized
      as unsupported_runtime_seam entries so the failure is explicit at invoke time.
    """
    replay_blocks = _extract_resource_reference_replay_blocks(result)
    uri_blocks = _extract_resource_reference_blocks(result)

    if not replay_blocks and not uri_blocks:
        return

    manifest = _get_media_manifest(session)
    profile = _get_session_capability_profile(session)

    # Persist replay blocks (embedded data stored in Ralph manifest)
    if replay_blocks and manifest is not None:
        for block in replay_blocks:
            uri = block.get("uri", "")
            artifact_id = parse_media_uri(uri)
            if artifact_id is None:
                continue
            entry = manifest.get(artifact_id)
            if entry is None:
                continue
            verdict = profile.verdict_for(entry.modality)
            raw_bytes = entry.load_bytes()
            if raw_bytes is None:
                continue
            cache_path = _write_durable_media_cache(workspace, artifact_id, raw_bytes)
            identity_key = entry.identity_key or build_media_identity(
                modality=entry.modality,
                mime_type=entry.mime_type,
                title=entry.title,
                raw_bytes=raw_bytes,
            )
            entry.set_replay_source(
                cache_path=cache_path,
                byte_loader=_workspace_artifact_loader(workspace, cache_path, ""),
            )
            _persist_media_session_entry(
                session,
                workspace,
                {
                    "uri": uri,
                    "mime_type": entry.mime_type,
                    "title": entry.title,
                    "modality": entry.modality,
                    "delivery": "resource_reference_replay",
                    "reason": verdict.reason,
                    "source_path": "",
                    "cache_path": cache_path,
                    "source_uri": "",
                    "block_type": verdict.block_type or "",
                    "identity_key": identity_key,
                },
            )

    # Synthesize unsupported_runtime_seam entries for URI-backed blocks
    # These reference external URIs and cannot be replayed across sessions
    if uri_blocks:
        for block in uri_blocks:
            uri = block.get("uri", "")
            modality = block.get("modality", "unknown")
            title = block.get("title", uri.rsplit("/", maxsplit=1)[-1] or "untitled")
            mime_type = block.get("mimeType", "application/octet-stream")
            source_uri = uri
            reason = (
                f"Active runtime seam cannot carry {modality} content through the handoff path. "
                f"External URI-backed artifacts are not replayable across sessions."
            )
            _persist_media_session_entry(
                session,
                workspace,
                {
                    "uri": uri,
                    "mime_type": mime_type,
                    "title": title,
                    "modality": modality,
                    "delivery": "unsupported",
                    "reason": reason,
                    "source_path": "",
                    "cache_path": "",
                    "source_uri": source_uri,
                    "block_type": "",
                    "failure_kind": "unsupported_runtime_seam",
                    "identity_key": build_media_identity(
                        modality=modality,
                        mime_type=mime_type,
                        title=title,
                        source_uri=source_uri,
                    ),
                },
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
    "handle_read_media",
    "handle_read_multiple_files",
    "handle_search_files",
    "handle_stat",
    "handle_write_file",
    "persist_upstream_media_artifacts",
    "required_string_param",
]
