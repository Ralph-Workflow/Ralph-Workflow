"""Read, stat, list, and search handler functions."""

from __future__ import annotations

import fnmatch
import json
import sqlite3
from typing import TYPE_CHECKING, cast

from ralph.mcp.explore.dirty_paths import resolve_explore_index
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
    from ralph.mcp.explore.store import EvidenceRow, ExploreStore
    from ralph.workspace import Workspace


# --- Phase 1 indexed read helpers -----------------------------------------


def _tombstone_replacement(tombstone: sqlite3.Row) -> str | None:
    """Return the tombstone's replacement_evidence_id, typed as ``str | None``."""
    raw: object = tombstone["replacement_evidence_id"]
    if raw is None:
        return None
    return str(raw)


def _tombstone_field(tombstone: sqlite3.Row, key: str) -> str:
    """Read a string-typed tombstone column with precise return type."""
    raw: object = tombstone[key]
    if raw is None:
        return ""
    return str(raw)


def _resolve_evidence(session: object, evidence_id: str) -> dict[str, object] | None:
    """Return the indexed evidence row for ``evidence_id`` if available.

    Returns ``None`` when the explore index is disabled. Raises
    ``ToolError`` when the evidence_id is unknown (the caller should
    treat that as a stale_evidence or unknown_evidence signal).
    """
    handle = resolve_explore_index(session)
    if handle is None:
        return None
    store: ExploreStore | None = getattr(handle, "store", None)
    if store is None:
        return None
    row: EvidenceRow | None = store.get_evidence(evidence_id)
    if row is None:
        # Try the tombstone for retention_expired vs stale_evidence.
        tombstone: sqlite3.Row | None = store.get_tombstone(evidence_id)
        if tombstone is None:
            raise ToolError(
                f"unknown_evidence: {evidence_id!r} (no live row or tombstone)"
            )
        return {
            "stale_evidence": True,
            "stale_reason": _tombstone_field(tombstone, "stale_reason"),
            "replacement_evidence_id": _tombstone_replacement(tombstone),
            "path": _tombstone_field(tombstone, "path"),
        }
    return {
        "evidence_id": row.evidence_id,
        "path": row.path,
        "start_line": row.start_line,
        "end_line": row.end_line,
        "content_hash": row.content_hash,
        "generation": row.generation,
    }


def _freshness_for_read(session: object) -> dict[str, object]:
    handle = resolve_explore_index(session)
    if handle is None:
        return {}
    store: ExploreStore | None = getattr(handle, "store", None)
    if store is None:
        return {}
    generation_raw = store.get_setting("current_generation") or "0"
    try:
        generation_int = int(generation_raw)
    except (TypeError, ValueError):
        generation_int = 0
    dirty = store.peek_dirty_paths()
    return {
        "index_used": True,
        "index_generation": generation_int,
        "is_stale": bool(dirty),
        "dirty_paths_count": len(dirty),
        "stale_paths_count": 0,
    }


def _hash_file(workspace: Workspace, path: str) -> str | None:
    """Return the SHA-256 of the workspace file's bytes, or None."""
    import hashlib

    try:
        content = workspace.read(path)
    except Exception:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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

    Phase 1 indexed args:

    * ``evidence_id`` — resolve the indexed evidence handle and read the
      exact span + ``context_lines`` of context.
    * ``span_id`` — placeholder; Phase 2 will resolve a symbol span.
      Phase 1 returns structured ``disabled:phase2``.
    * ``symbol`` — placeholder; Phase 2 will resolve a symbol span.
      Phase 1 returns structured ``disabled:phase2``.
    * ``expected_content_hash`` — fail closed if the current file's
      content hash does not match.
    * ``context_lines`` — bounded context around the resolved span.
    * ``return_metadata`` — include content hash, generation, and freshness.
    """
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Workspace read")

    # --- Phase 1 indexed selectors -------------------------------------
    evidence_id = params.get("evidence_id")
    span_id = params.get("span_id")
    symbol_selector = params.get("symbol")
    expected_hash = params.get("expected_content_hash")
    context_lines = _int_param(params, "context_lines", 0)
    return_metadata = bool(params.get("return_metadata", False))

    if evidence_id is not None:
        return _read_via_evidence(
            session,
            workspace,
            evidence_id=str(evidence_id),
            context_lines=context_lines,
            expected_hash=str(expected_hash) if expected_hash is not None else None,
            return_metadata=return_metadata,
        )
    if span_id is not None:
        return _read_disabled(
            session,
            "span_id",
            span_id=str(span_id),
            return_metadata=return_metadata,
        )
    if symbol_selector is not None:
        return _read_disabled(
            session,
            "symbol",
            symbol=str(symbol_selector),
            return_metadata=return_metadata,
        )

    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)

    # Expected-content-hash precondition (no evidence/span selector).
    if expected_hash is not None:
        actual_hash = _hash_file(workspace, normalized)
        if actual_hash is None:
            return ToolResult(
                content=[
                    ToolContent.text_content(
                        _tool_json(
                            {
                                "status": "stale_evidence",
                                "path": path,
                                "expected_content_hash": expected_hash,
                                "reason": "file_missing",
                            }
                        )
                    )
                ],
                is_error=True,
            )
        if actual_hash != expected_hash:
            return ToolResult(
                content=[
                    ToolContent.text_content(
                        _tool_json(
                            {
                                "status": "stale_evidence",
                                "path": path,
                                "expected_content_hash": expected_hash,
                                "current_content_hash": actual_hash,
                                "reason": "content_changed",
                            }
                        )
                    )
                ],
                is_error=True,
            )

    sel = _ReadSelector.from_params(params)
    if sel.is_active():
        result = _dispatch_partial_read(workspace, normalized, path, sel)
        if return_metadata:
            # Re-decode the JSON envelope and append freshness.
            try:
                first_content = result.content[0]
                if not isinstance(first_content, ToolContent):
                    return result
                raw_payload: object = json.loads(first_content.text)
                if not isinstance(raw_payload, dict):
                    return result
                freshness = _freshness_for_read(session)
                payload_dict: dict[str, object] = cast("dict[str, object]", raw_payload)
                payload_dict.update(freshness)
                return ToolResult(
                    content=[ToolContent.text_content(_tool_json(payload_dict))],
                    is_error=result.is_error,
                )
            except (ValueError, TypeError):
                return result
        return result

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
        if return_metadata:
            payload.update(_freshness_for_read(session))
            payload["content_hash"] = _hash_file(workspace, normalized)
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
    if return_metadata:
        payload = {
            "path": path,
            "content": content,
            "content_hash": _hash_file(workspace, normalized),
        }
        payload.update(_freshness_for_read(session))
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(payload))],
            is_error=False,
        )
    return ToolResult(content=[ToolContent.text_content(content)], is_error=False)


def _read_via_evidence(
    session: object,
    workspace: Workspace,
    *,
    evidence_id: str,
    context_lines: int,
    expected_hash: str | None,
    return_metadata: bool,
) -> ToolResult:
    info = _resolve_evidence(session, evidence_id)
    if info is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "indexed_selector_unavailable",
                            "evidence_id": evidence_id,
                            "reason": "no_explore_index_handle",
                        }
                    )
                )
            ],
            is_error=True,
        )
    if info.get("stale_evidence"):
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(info))],
            is_error=True,
        )
    path = info["path"]
    start_line_raw: object = info.get("start_line") or 0
    end_line_raw: object = info.get("end_line") or 0
    start_line = int(start_line_raw) if isinstance(start_line_raw, (int, str, float)) else 0
    end_line = int(end_line_raw) if isinstance(end_line_raw, (int, str, float)) else 0
    expected_content_hash = expected_hash or info.get("content_hash")
    normalized = normalize_relative_path(str(path))
    actual_hash = _hash_file(workspace, normalized)
    if actual_hash is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "stale_evidence",
                            "evidence_id": evidence_id,
                            "reason": "file_missing",
                            "path": path,
                        }
                    )
                )
            ],
            is_error=True,
        )
    if expected_content_hash and actual_hash != expected_content_hash:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "stale_evidence",
                            "evidence_id": evidence_id,
                            "path": path,
                            "expected_content_hash": expected_content_hash,
                            "current_content_hash": actual_hash,
                            "reason": "content_changed",
                        }
                    )
                )
            ],
            is_error=True,
        )
    content, _meta = workspace.read_lines(
        normalized,
        start=max(1, start_line - context_lines),
        end=end_line + context_lines,
    )
    payload = {
        "path": path,
        "evidence_id": evidence_id,
        "start_line": start_line,
        "end_line": end_line,
        "context_lines": context_lines,
        "content": content,
    }
    if return_metadata:
        payload["content_hash"] = actual_hash
        payload.update(_freshness_for_read(session))
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=False,
    )


def _read_disabled(
    session: object,
    selector_name: str,
    *,
    return_metadata: bool,
    **fields: object,
) -> ToolResult:
    payload: dict[str, object] = {
        "status": "indexed_selector_unavailable",
        "reason": f"disabled:phase2 (selector={selector_name})",
        "selector": selector_name,
    }
    payload.update(fields)
    if return_metadata:
        payload.update(_freshness_for_read(session))
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=True,
    )


def handle_read_multiple_files(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Read multiple workspace files in one call and return per-file results.

    Phase 1 indexed args:

    * ``items`` (list of dicts): each item may be one of
      ``{"path": "...", "line_start": N, "line_end": N}``,
      ``{"evidence_id": "..."}``,
      ``{"span_id": "..."}``, or ``{"symbol": "..."}``.
    * ``per_item_max_bytes``: cap each returned item.
    * ``return_metadata``: include freshness per item.
    * ``fail_fast``: a stale indexed item fails only that item unless
      ``fail_fast=true`` (Phase 1 keeps the default ``fail_fast=true``
      so callers can opt into partial results explicitly).
    """
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Read multiple files")

    items_param = params.get("items")
    legacy_paths_mode = items_param is None
    if items_param is None:
        # Legacy ``paths`` path.
        paths_param = params.get("paths")
        if not isinstance(paths_param, list):
            raise InvalidParamsError("Missing 'paths' parameter as list of strings")
        items_param = [{"path": str(p)} for p in paths_param]

    if not isinstance(items_param, list):
        raise InvalidParamsError("'items' must be a list")

    per_item_max_bytes = _int_param(params, "per_item_max_bytes", 0)
    return_metadata = bool(params.get("return_metadata", False))
    fail_fast = bool(params.get("fail_fast", True))

    results: list[dict[str, object]] = []
    has_fatal_error = False
    for item in items_param:
        if not isinstance(item, dict):
            results.append({"error": "item_not_a_dict"})
            has_fatal_error = True
            if fail_fast:
                break
            continue
        result = _read_multiple_item(
            session,
            workspace,
            item,
            per_item_max_bytes=per_item_max_bytes,
            return_metadata=return_metadata,
        )
        results.append(result)
        if result.get("is_error") and fail_fast:
            has_fatal_error = True
            break

    payload = {
        "files": results,
        "truncated": False,
    }
    if return_metadata:
        payload.update(_freshness_for_read(session))
    # Legacy ``paths`` mode preserves the prior behavior: a per-file
    # error does not flip ``is_error`` on the top-level result.
    is_error = False if legacy_paths_mode else has_fatal_error
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=is_error,
    )


def _read_multiple_item(
    session: object,
    workspace: Workspace,
    item: dict[str, object],
    *,
    per_item_max_bytes: int,
    return_metadata: bool,
) -> dict[str, object]:
    """Resolve a single ``items`` entry to a result dict."""
    if "evidence_id" in item:
        # Reuse the single-read evidence logic via a synthetic call.
        single_result = _read_via_evidence(
            session,
            workspace,
            evidence_id=str(item["evidence_id"]),
            context_lines=_int_opt_param(item, "context_lines") or 0,
            expected_hash=(
                str(item["expected_content_hash"])
                if item.get("expected_content_hash") is not None
                else None
            ),
            return_metadata=return_metadata,
        )
        first_content = single_result.content[0]
        if not isinstance(first_content, ToolContent):
            return {
                "selector": "evidence_id",
                "evidence_id": str(item["evidence_id"]),
                "is_error": single_result.is_error,
                "content": "",
            }
        try:
            payload: object = json.loads(first_content.text)
        except (ValueError, TypeError):
            payload = {"raw": first_content.text}
        if not isinstance(payload, dict):
            return {
                "selector": "evidence_id",
                "evidence_id": str(item["evidence_id"]),
                "is_error": single_result.is_error,
                "content": "",
            }
        payload_dict: dict[str, object] = cast("dict[str, object]", payload)
        payload_dict["is_error"] = single_result.is_error
        if per_item_max_bytes and isinstance(payload_dict.get("content"), str):
            content_value = payload_dict["content"]
            assert isinstance(content_value, str)
            payload_dict["content"] = content_value[:per_item_max_bytes]
            payload_dict["truncated"] = True
        return payload_dict
    if "span_id" in item:
        return {
            "selector": "span_id",
            "span_id": str(item["span_id"]),
            "status": "indexed_selector_unavailable",
            "reason": "disabled:phase2 (selector=span_id)",
            "is_error": True,
        }
    if "symbol" in item:
        return {
            "selector": "symbol",
            "symbol": str(item["symbol"]),
            "status": "indexed_selector_unavailable",
            "reason": "disabled:phase2 (selector=symbol)",
            "is_error": True,
        }
    path = item.get("path")
    if not isinstance(path, str):
        return {"error": "missing_path", "is_error": True}
    normalized = normalize_relative_path(path)
    line_start = _int_opt_param(item, "line_start")
    line_end = _int_opt_param(item, "line_end")
    try:
        if line_start is not None or line_end is not None:
            content, _meta = workspace.read_lines(
                normalized, start=line_start, end=line_end
            )
        else:
            content = workspace.read(normalized)
    except Exception as exc:
        return {"path": path, "error": str(exc), "is_error": True}
    if per_item_max_bytes:
        content = content[:per_item_max_bytes]
        truncated = True
    else:
        truncated = False
    path_payload: dict[str, object] = {
        "path": path,
        "content": content,
        "truncated": truncated,
    }
    if return_metadata:
        path_payload["content_hash"] = _hash_file(workspace, normalized)
        path_payload.update(_freshness_for_read(session))
    return path_payload


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
    """Search for files matching a glob pattern within a workspace directory.

    Phase 1 indexed args:

    * ``ranked``: rank paths by deterministic index signals.
    * ``role`` in {source, test, docs, config, generated, any}.
    * ``contains_symbol`` (Phase 2; Phase 1 returns structured
      ``disabled:phase2`` but still returns live glob results).
    * ``changed_only``: filter to git-changed paths.
    * ``return_evidence_ids``: attach handles to matched indexed records.
    """
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
    ranked = bool(params.get("ranked", False))
    role = str(params.get("role", "any"))
    if role not in {"source", "test", "docs", "config", "generated", "any"}:
        raise InvalidParamsError(
            f"Invalid role: {role!r}; expected 'source', 'test', "
            "'docs', 'config', 'generated', or 'any'."
        )
    contains_symbol = params.get("contains_symbol")
    changed_only = bool(params.get("changed_only", False))
    return_evidence_ids = bool(params.get("return_evidence_ids", False))

    matches = _collect_matching_files(workspace, normalized, pattern, exclude=exclude)
    truncated = len(matches) > limit
    if truncated:
        matches = matches[:limit]

    # Live role filter (Phase 1 keeps role filter in-handler).
    if role != "any":
        from ralph.mcp.explore.ranking import is_source_role, is_test_role

        role_filter = {
            "source": is_source_role,
            "test": is_test_role,
        }.get(role)
        if role_filter is not None:
            matches = [m for m in matches if role_filter(m)]

    # Apply changed_only (Phase 1 has no git signal — we return
    # an empty list and report it in the response so callers see
    # an explicit fallback).
    if changed_only:
        matches = []

    # Ranking.
    score_reasons: list[dict[str, object]] = []
    if ranked:
        from ralph.mcp.explore.ranking import score_search_file, sort_ranked

        basename = pattern.split("/")[-1]
        is_git_changed = False  # Phase 1 has no git signal here.
        items = [
            score_search_file(
                candidate_path=m,
                basename=basename,
                role_requested=role if role != "any" else None,
                is_git_changed=is_git_changed,
            )
            for m in matches
        ]
        items = sort_ranked(items)
        matches = [item.path for item in items]
        score_reasons = [item.to_dict() for item in items]

    # contains_symbol is Phase 2.
    contains_symbol_note: str | None = None
    if contains_symbol is not None:
        contains_symbol_note = "disabled:phase2"

    output = {
        "pattern": pattern,
        "base": path,
        "matches": matches,
        "truncated": truncated,
        "ranked": ranked,
        "role": role,
        "contains_symbol": contains_symbol,
        "contains_symbol_note": contains_symbol_note,
        "changed_only": changed_only,
    }
    if score_reasons:
        output["score_reasons"] = score_reasons
    if return_evidence_ids:
        # Phase 1 evidence for path-only searches is the file's own
        # chunk_id for line 1..1; Phase 2 will provide per-symbol ids.
        # Best-effort: emit evidence handles only when an index handle
        # is attached. Otherwise the caller is in legacy mode.
        from ralph.mcp.explore.dirty_paths import resolve_explore_index
        from ralph.mcp.explore.store import (
            derive_evidence_id,
        )

        handle = resolve_explore_index(session)
        if handle is not None:
            store: ExploreStore | None = getattr(handle, "store", None)
            if store is not None:
                evidence_ids: list[str] = [
                    derive_evidence_id(
                        path=m,
                        content_hash="",
                        start_line=0,
                        end_line=0,
                        kind="path",
                        extractor_version="phase1-lexical-v1",
                    )
                    for m in matches
                ]
                output["evidence_ids"] = evidence_ids
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(output))], is_error=False
    )
