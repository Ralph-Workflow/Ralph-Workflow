"""Read, stat, list, and search handler functions."""

from __future__ import annotations

import fnmatch
import json
import sqlite3
import time
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, cast

from ralph.mcp.explore.dirty_paths import (
    ExploreIndexLike,
    resolve_explore_index,
)
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
    from ralph.mcp.explore.store import EvidenceRow, ExploreStore, SpanRow, SymbolRow
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
    * ``span_id`` — resolve the indexed span from the structure rows
      and read the exact span + bounded context. Behavior is
      fail-closed: a stale span, missing generation, or unknown
      span id produces a structured ``unknown_evidence`` or
      ``stale_evidence`` response.
    * ``symbol`` — resolve the indexed symbol span from the
      structure rows and read the exact span + bounded context.
      Behavior is fail-closed: a stale symbol, missing generation,
      or unknown symbol produces a structured ``unknown_evidence``
      or ``stale_evidence`` response.
    * ``expected_content_hash`` — fail closed if the current file's
      content hash does not match.
    * ``context_lines`` — bounded context around the resolved span.
    * ``return_metadata`` — include content hash, generation, and freshness.
    """
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Workspace read")

    # --- Indexed selectors -------------------------------------------
    # AC-01: the read_file handler accepts exactly one of
    # ``path``, ``evidence_id``, ``span_id``, or ``symbol``. Direct
    # callers (without the JSON Schema ``oneOf`` validator) are
    # rejected here too so an in-process bug or hand-written
    # client cannot silently pick a selector. Empty (no selector)
    # is rejected because every selector is required.
    evidence_id = params.get("evidence_id")
    span_id = params.get("span_id")
    symbol_selector = params.get("symbol")
    path_selector = params.get("path")
    selected_selectors: list[str] = []
    if path_selector is not None:
        selected_selectors.append("path")
    if evidence_id is not None:
        selected_selectors.append("evidence_id")
    if span_id is not None:
        selected_selectors.append("span_id")
    if symbol_selector is not None:
        selected_selectors.append("symbol")
    if not selected_selectors:
        raise InvalidParamsError(
            "read_file requires exactly one of: "
            "path, evidence_id, span_id, or symbol."
        )
    if len(selected_selectors) > 1:
        raise InvalidParamsError(
            "read_file accepts exactly one selector; received: "
            + ", ".join(selected_selectors)
            + "."
        )
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
        return _read_via_span(
            session,
            workspace,
            span_id=str(span_id),
            context_lines=context_lines,
            expected_hash=str(expected_hash) if expected_hash is not None else None,
            return_metadata=return_metadata,
        )
    if symbol_selector is not None:
        return _read_via_symbol(
            session,
            workspace,
            symbol=str(symbol_selector),
            context_lines=context_lines,
            expected_hash=str(expected_hash) if expected_hash is not None else None,
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


# Distinct sentinels so the resolvers can report "no index handle"
# versus "index present but selector unknown" without collapsing
# the two outcomes into the same None return.
_SPAN_NOT_FOUND = object()
_SYMBOL_NOT_FOUND = object()
_SYMBOL_AMBIGUOUS = object()


def _resolve_span(session: object, span_id: str) -> object | None:
    """Return the indexed ``SpanRow`` for ``span_id`` or a sentinel.

    Return values:

    * ``None`` — the session has no explore index handle.
    * ``_SPAN_NOT_FOUND`` — the handle is attached but the span is
      not in the store.
    * :class:`SpanRow` — the resolved row.
    """
    handle = resolve_explore_index(session)
    if handle is None:
        return None
    store: ExploreStore | None = getattr(handle, "store", None)
    if store is None:
        return None
    span_row = store.get_span(span_id)
    if span_row is None:
        return _SPAN_NOT_FOUND
    return span_row


def _resolve_symbol(
    session: object,
    symbol: str,
    *,
    path: str | None = None,
) -> object | None:
    """Return the indexed symbol resolution for ``symbol``.

    Return values:

    * ``None`` — the session has no explore index handle.
    * ``_SYMBOL_NOT_FOUND`` — the handle is attached but the symbol
      is not in the store.
    * ``_SYMBOL_AMBIGUOUS`` — multiple candidates matched; the
      caller should surface a structured ambiguity error.
    * :class:`SymbolRow` — the resolved unique match.
    """
    handle = resolve_explore_index(session)
    if handle is None:
        return None
    store: ExploreStore | None = getattr(handle, "store", None)
    if store is None:
        return None
    if "." in symbol or "::" in symbol:
        candidates = store.find_symbols(qualified_name=symbol, path=path)
    else:
        candidates = store.find_symbols(name=symbol, path=path)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return _SYMBOL_NOT_FOUND
    return _SYMBOL_AMBIGUOUS


def _read_via_span(
    session: object,
    workspace: Workspace,
    *,
    span_id: str,
    context_lines: int,
    expected_hash: str | None,
    return_metadata: bool,
) -> ToolResult:
    span_obj = _resolve_span(session, span_id)
    if span_obj is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "indexed_selector_unavailable",
                            "span_id": span_id,
                            "reason": "no_explore_index_handle",
                        }
                    )
                )
            ],
            is_error=True,
        )
    if span_obj is _SPAN_NOT_FOUND:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "unknown_evidence",
                            "span_id": span_id,
                            "reason": "no_matching_span",
                        }
                    )
                )
            ],
            is_error=True,
        )
    # ``span_obj`` is a SpanRow at this point — typed access is safe.
    span_row: SpanRow = cast("SpanRow", span_obj)
    path = span_row.path
    start_line = int(span_row.start_line)
    end_line = int(span_row.end_line)
    span_content_hash = span_row.content_hash
    expected_content_hash = expected_hash or span_content_hash
    normalized = normalize_relative_path(str(path))
    actual_hash = _hash_file(workspace, normalized)
    if actual_hash is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "stale_evidence",
                            "span_id": span_id,
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
                            "span_id": span_id,
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
    payload: dict[str, object] = {
        "path": path,
        "span_id": span_id,
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


def _read_via_symbol(
    session: object,
    workspace: Workspace,
    *,
    symbol: str,
    context_lines: int,
    expected_hash: str | None,
    return_metadata: bool,
) -> ToolResult:
    sym_obj = _resolve_symbol(session, symbol)
    if sym_obj is None:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "indexed_selector_unavailable",
                            "symbol": symbol,
                            "reason": "no_explore_index_handle",
                        }
                    )
                )
            ],
            is_error=True,
        )
    if sym_obj is _SYMBOL_NOT_FOUND:
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "unknown_evidence",
                            "symbol": symbol,
                            "reason": "no_matching_symbol",
                        }
                    )
                )
            ],
            is_error=True,
        )
    if sym_obj is _SYMBOL_AMBIGUOUS:
        # The store knows the candidates; refetch the list and
        # surface them so the caller can disambiguate.
        handle = resolve_explore_index(session)
        candidates: Sequence[SymbolRow] = []
        if handle is not None:
            store: ExploreStore | None = getattr(handle, "store", None)
            if store is not None:
                if "." in symbol or "::" in symbol:
                    candidates = store.find_symbols(qualified_name=symbol)
                else:
                    candidates = store.find_symbols(name=symbol)
        candidate_paths = [
            {
                "path": c.path,
                "qualified_name": c.qualified_name,
            }
            for c in candidates
        ]
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "ambiguous_symbol",
                            "symbol": symbol,
                            "candidates": candidate_paths,
                        }
                    )
                )
            ],
            is_error=True,
        )
    sym_row: SymbolRow = cast("SymbolRow", sym_obj)
    path = sym_row.path
    span_id = sym_row.span_id
    # Defer to the span resolver so hash checks and content slicing
    # share one code path.
    if span_id:
        return _read_via_span(
            session,
            workspace,
            span_id=str(span_id),
            context_lines=context_lines,
            expected_hash=expected_hash,
            return_metadata=return_metadata,
        )
    # Fallback: span_id missing — return the symbol's metadata with
    # a structured "ambiguous" payload so callers can rerun with a
    # more specific selector.
    payload_ambiguous: dict[str, object] = {
        "status": "ambiguous_symbol",
        "symbol": symbol,
        "path": path,
    }
    if return_metadata:
        payload_ambiguous.update(_freshness_for_read(session))
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload_ambiguous))],
        is_error=True,
    )





def _decode_payload(result: ToolResult) -> dict[str, object]:
    """Decode a ``ToolResult`` JSON envelope into a mutable dict.

    Used by ``read_multiple_files`` to splice indexed span / symbol
    results into the per-item payload without losing the structured
    error semantics from the helpers above. Empty dicts are returned
    on decode failure so callers can still attach selector metadata
    without crashing.
    """
    if not result.content:
        return {"content": ""}
    first_content = result.content[0]
    if not isinstance(first_content, ToolContent):
        return {"content": ""}
    try:
        payload: object = json.loads(first_content.text)
    except (ValueError, TypeError):
        return {"raw": first_content.text}
    if isinstance(payload, dict):
        return cast("dict[str, object]", payload)
    return {"raw": first_content.text}


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
    paths_param = params.get("paths")

    # AC-01: read_multiple_files accepts exactly one of ``paths``
    # (legacy live read) or ``items`` (mixed live/indexed reads).
    # Empty / conflicting / both-present inputs are rejected
    # explicitly so direct callers cannot silently choose a
    # selector.
    if items_param is not None and paths_param is not None:
        raise InvalidParamsError(
            "read_multiple_files accepts exactly one of 'paths' or 'items'."
        )
    legacy_paths_mode = items_param is None
    if items_param is None:
        # Legacy ``paths`` path.
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
    """Resolve a single ``items`` entry to a result dict.

    AC-01: each ``items`` entry must specify exactly one selector
    (``path``, ``evidence_id``, ``span_id``, or ``symbol``). Empty
    or conflicting selectors are rejected with a structured
    ``invalid_selector`` error so direct callers cannot silently
    pick a selector.
    """
    _selector_keys = ("path", "evidence_id", "span_id", "symbol")
    present_selectors = [key for key in _selector_keys if key in item]
    if not present_selectors:
        return {
            "is_error": True,
            "error": "invalid_selector",
            "reason": (
                "items entry requires exactly one of: "
                "path, evidence_id, span_id, or symbol."
            ),
        }
    if len(present_selectors) > 1:
        return {
            "is_error": True,
            "error": "invalid_selector",
            "reason": (
                "items entry accepts exactly one selector; "
                "received: " + ", ".join(present_selectors) + "."
            ),
        }
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
        span_result = _read_via_span(
            session,
            workspace,
            span_id=str(item["span_id"]),
            context_lines=0,
            expected_hash=None,
            return_metadata=return_metadata,
        )
        span_payload = _decode_payload(span_result)
        span_payload["selector"] = "span_id"
        span_payload["is_error"] = span_result.is_error
        if per_item_max_bytes and isinstance(span_payload.get("content"), str):
            content_value = span_payload["content"]
            assert isinstance(content_value, str)
            span_payload["content"] = content_value[:per_item_max_bytes]
            span_payload["truncated"] = True
        return span_payload
    if "symbol" in item:
        symbol_result = _read_via_symbol(
            session,
            workspace,
            symbol=str(item["symbol"]),
            context_lines=0,
            expected_hash=None,
            return_metadata=return_metadata,
        )
        symbol_payload = _decode_payload(symbol_result)
        symbol_payload["selector"] = "symbol"
        symbol_payload["is_error"] = symbol_result.is_error
        if per_item_max_bytes and isinstance(symbol_payload.get("content"), str):
            content_value = symbol_payload["content"]
            assert isinstance(content_value, str)
            symbol_payload["content"] = content_value[:per_item_max_bytes]
            symbol_payload["truncated"] = True
        return symbol_payload
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
    """List entries in a workspace directory.

    AC-09 indexed args:

    * ``view`` ``raw|compact|ranked|outline`` — raw preserves
      the legacy shape; compact adds counts; ranked sorts by
      symbol count; outline includes top-level symbols/headings.
    * ``include_counts``, ``include_symbols``, ``changed_only``,
      ``limit_children`` filter the indexed view.
    * ``use_index`` ``auto|always|never`` — always fails closed
      when required indexed metadata cannot be supplied.
    """
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Directory listing")
    path = required_string_param(params, "path")
    recursive = bool(params.get("recursive", False))
    view = str(params.get("view", "raw"))
    if view not in {"raw", "compact", "ranked", "outline"}:
        raise InvalidParamsError(
            f"Invalid view: {view!r}; expected raw, compact, ranked, or outline"
        )
    use_index = str(params.get("use_index", "auto"))
    if use_index not in {"auto", "always", "never"}:
        raise InvalidParamsError(
            f"Invalid use_index: {use_index!r}; expected auto, always, or never"
        )
    include_counts = bool(params.get("include_counts", False))
    include_symbols = bool(params.get("include_symbols", False))
    changed_only = bool(params.get("changed_only", False))
    limit_children = _int_param(params, "limit_children", 100)

    # AC-09: ``use_index='never'`` is an unconditional bypass. The
    # caller has explicitly opted out, so the handler must never
    # resolve the explore index -- it must use the live listing path
    # regardless of view/recursive/depth. The same path also covers
    # the default-raw backward-compat case: when the caller did NOT
    # pass any indexed view selector (view=raw, no
    # include_counts/include_symbols/changed_only/use_index=always),
    # preserve the legacy plain listing shape even if an index
    # handle is attached.
    explicit_indexed_request = (
        view != "raw"
        or include_counts
        or include_symbols
        or changed_only
        or use_index == "always"
    )
    if use_index == "never" or not explicit_indexed_request:
        output = (
            list_dir_flat(workspace, path)
            if not recursive
            else _list_dir_recursive_output(workspace, path)
        )
        return ToolResult(
            content=[ToolContent.text_content(output)], is_error=False
        )

    # Indexed listing path.
    handle_obj: object = resolve_explore_index(session)
    handle_obj2: object | None = (
        handle_obj
        if handle_obj is not None and hasattr(handle_obj, "store")
        else None
    )
    handle: ExploreIndexLike | None = cast(
        "ExploreIndexLike | None", handle_obj2
    )
    raw_handle_store: object | None
    if handle is None:
        raw_handle_store = None
    else:
        store_attr: object = getattr(handle, "store", None)
        raw_handle_store = store_attr
    if handle is None or raw_handle_store is None:
        if use_index == "always":
            return ToolResult(
                content=[
                    ToolContent.text_content(
                        _tool_json(
                            {
                                "status": "indexed_view_unavailable",
                                "reason": "no_explore_index_handle",
                            }
                        )
                    )
                ],
                is_error=True,
            )
        # AC-09: when the caller explicitly asked for an indexed view
        # but no handle is attached, ``use_index='auto'`` falls back to
        # a documented live-fallback wrapper instead of silently
        # dropping the metadata. ``use_index='never'`` is handled
        # above; ``use_index='always'`` is handled by the early
        # return above.
        output = (
            list_dir_flat(workspace, path)
            if not recursive
            else _list_dir_recursive_output(workspace, path)
        )
        # The raw view + an explicit indexed view request is
        # contradictory; honour the raw view and emit a
        # fallback_reason so the caller can detect the downgrade.
        if view == "raw":
            return ToolResult(
                content=[
                    ToolContent.text_content(
                        _tool_json(
                            {
                                "entries": output,
                                "view": "raw",
                                "fallback_reason": "no_index_handle",
                            }
                        )
                    )
                ],
                is_error=False,
            )
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "entries": output,
                            "view": view,
                            "fallback_reason": "no_index_handle",
                            "include_counts": include_counts,
                            "include_symbols": include_symbols,
                            "changed_only": changed_only,
                            "limit_children": limit_children,
                        }
                    )
                )
            ],
            is_error=False,
        )

    entries = list_dir_flat(workspace, path)
    try:
        entries_parsed: object = json.loads(entries)
    except (ValueError, TypeError):
        entries_parsed = []
    entries_list: list[dict[str, object]] = (
        [e for e in entries_parsed if isinstance(e, dict)]
        if isinstance(entries_parsed, list)
        else []
    )
    raw_store: object | None = handle.store
    # Filter / prioritize changed paths when requested.
    if changed_only and raw_store is not None:
        dirty_obj: object = getattr(raw_store, "peek_dirty_paths", lambda: [])()
        dirty_iterable: Iterable[object] = (
            dirty_obj if isinstance(dirty_obj, list) else []
        )
        dirty: set[str] = {str(p) for p in dirty_iterable if isinstance(p, str)}
        filtered: list[dict[str, object]] = []
        for entry in entries_list:
            entry_path_obj: object = entry.get("path")
            if isinstance(entry_path_obj, str) and entry_path_obj in dirty:
                filtered.append(entry)
        entries_list = filtered
    # Symbol counts and headings.
    if (
        raw_store is not None
        and (
            include_counts
            or include_symbols
            or view in {"compact", "ranked", "outline"}
        )
    ):
        counts_by_path: dict[str, dict[str, int]] = {}
        symbols_by_path: dict[str, list[dict[str, str]]] = {}
        sym_iter_obj: object = getattr(raw_store, "iter_symbols", lambda: [])()
        sym_iterable: Iterable[object] = (
            sym_iter_obj if hasattr(sym_iter_obj, "__iter__") else []
        )
        for sym in sym_iterable:
            sym_path_obj: object = getattr(sym, "path", "")
            sym_name_obj: object = getattr(sym, "name", "")
            sym_kind_obj: object = getattr(sym, "kind", "")
            sym_path = sym_path_obj if isinstance(sym_path_obj, str) else str(sym_path_obj)
            sym_name = sym_name_obj if isinstance(sym_name_obj, str) else str(sym_name_obj)
            sym_kind = sym_kind_obj if isinstance(sym_kind_obj, str) else str(sym_kind_obj)
            counts_by_path.setdefault(sym_path, {"symbols": 0})
            counts_by_path[sym_path]["symbols"] += 1
            if include_symbols or view == "outline":
                symbols_by_path.setdefault(sym_path, []).append(
                    {"name": sym_name, "kind": sym_kind}
                )
        for entry in entries_list:
            entry_path_obj2: object = entry.get("path")
            entry_path: str = (
                entry_path_obj2
                if isinstance(entry_path_obj2, str)
                else (str(entry_path_obj2) if entry_path_obj2 is not None else "")
            )
            if not entry_path:
                continue
            if include_counts or view in {"compact", "ranked"}:
                entry["counts"] = counts_by_path.get(entry_path, {"symbols": 0})
            if include_symbols or view == "outline":
                entry["symbols"] = symbols_by_path.get(entry_path, [])
    if view == "ranked":
        def _rank_key(e: object) -> tuple[int, str]:
            if not isinstance(e, dict):
                return (0, "")
            counts_obj: object = e.get("counts", {})
            symbols_count = 0
            if isinstance(counts_obj, dict):
                count_val: object = counts_obj.get("symbols", 0)
                if isinstance(count_val, int):
                    symbols_count = count_val
            name_obj: object = e.get("name", "")
            name = name_obj if isinstance(name_obj, str) else str(name_obj)
            return (-symbols_count, name)

        entries_list = sorted(entries_list, key=_rank_key)
    if limit_children > 0:
        entries_list = entries_list[:limit_children]
    is_stale: bool = False
    if raw_store is not None:
        dirty_again_obj: object = getattr(raw_store, "peek_dirty_paths", lambda: [])()
        if isinstance(dirty_again_obj, list):
            is_stale = bool(dirty_again_obj)
        elif isinstance(dirty_again_obj, bool):
            is_stale = dirty_again_obj
        else:
            is_stale = bool(dirty_again_obj)
    payload: dict[str, object] = {
        "entries": entries_list,
        "view": view,
        "changed_only": changed_only,
        "include_counts": include_counts,
        "include_symbols": include_symbols,
        "limit_children": limit_children,
        "index_used": True,
        "is_stale": is_stale,
    }
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))], is_error=False
    )


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
    """Build a recursive directory tree structure.

    AC-09: every node carries its full ``path`` so the directory_tree
    handler can look up indexed counts/symbols and run
    ``changed_only`` filters without rebuilding the tree. The
    ``path`` is the workspace-relative POSIX path used by the
    explore index.
    """
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
        return {"name": name, "type": "file", "path": normalized}

    if max_depth is not None and current_depth >= max_depth:
        return {"name": name, "type": "dir", "path": normalized, "children": []}

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

    return {"name": name, "type": "dir", "path": normalized, "children": entries}


def handle_directory_tree(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Return a nested JSON directory tree for a workspace path.

    AC-09 indexed args mirror ``list_directory``: ``view``,
    ``include_counts``, ``include_symbols``, ``limit_children``,
    ``use_index``. Raw + never preserve the legacy tree shape.
    """
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Directory tree")
    path = required_string_param(params, "path")
    max_depth = _int_opt_param(params, "max_depth")
    exclude_patterns = params.get("exclude_patterns")
    if exclude_patterns and isinstance(exclude_patterns, list):
        exclude_patterns = [str(p) for p in exclude_patterns]
    else:
        exclude_patterns = None
    view = str(params.get("view", "raw"))
    if view not in {"raw", "compact", "ranked", "outline"}:
        raise InvalidParamsError(
            f"Invalid view: {view!r}; expected raw, compact, ranked, or outline"
        )
    use_index = str(params.get("use_index", "auto"))
    if use_index not in {"auto", "always", "never"}:
        raise InvalidParamsError(
            f"Invalid use_index: {use_index!r}; expected auto, always, or never"
        )
    include_counts = bool(params.get("include_counts", False))
    include_symbols = bool(params.get("include_symbols", False))
    limit_children = _int_param(params, "limit_children", 100)
    changed_only = bool(params.get("changed_only", False))

    # AC-09: ``use_index='never'`` is an unconditional bypass. The
    # caller has explicitly opted out, so the handler must never
    # resolve the explore index or decorate the tree. The same path
    # also covers the default-raw backward-compat case: when the
    # caller did NOT pass any indexed view selector (view=raw, no
    # include_counts/include_symbols/changed_only/use_index=always),
    # preserve the legacy tree shape even if an index handle is
    # attached.
    explicit_indexed_request = (
        view != "raw"
        or include_counts
        or include_symbols
        or changed_only
        or use_index == "always"
    )
    if use_index == "never" or not explicit_indexed_request:
        try:
            tree_obj: object = _build_directory_tree(
                workspace, path, 0, max_depth, exclude_patterns
            )
        except Exception as exc:
            raise ToolError(
                f"Failed to build directory tree for '{path}': {exc}"
            ) from exc
        tree_dict: dict[str, object] = (
            tree_obj if isinstance(tree_obj, dict) else {}
        )
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(tree_dict))],
            is_error=False,
        )

    handle_obj_check: object = resolve_explore_index(session)
    handle2: ExploreIndexLike | None
    if handle_obj_check is not None and hasattr(handle_obj_check, "store"):
        handle2 = cast("ExploreIndexLike | None", handle_obj_check)
    else:
        handle2 = None
    handle = handle2
    handle_check_store: object | None = (
        None
        if handle is None
        else cast("object", getattr(handle, "store", None))
    )
    if handle is None or handle_check_store is None:
        if use_index == "always":
            return ToolResult(
                content=[
                    ToolContent.text_content(
                        _tool_json(
                            {
                                "status": "indexed_view_unavailable",
                                "reason": "no_explore_index_handle",
                            }
                        )
                    )
                ],
                is_error=True,
            )
        try:
            tree_obj2: object = _build_directory_tree(
                workspace, path, 0, max_depth, exclude_patterns
            )
        except Exception as exc:
            raise ToolError(
                f"Failed to build directory tree for '{path}': {exc}"
            ) from exc
        tree_dict2: dict[str, object] = (
            tree_obj2 if isinstance(tree_obj2, dict) else {}
        )
        # Legacy shape when no index is attached and the caller did
        # not explicitly request an indexed view.
        if view == "raw":
            return ToolResult(
                content=[ToolContent.text_content(_tool_json(tree_dict2))],
                is_error=False,
            )
        payload_fallback: dict[str, object] = {
            "tree": tree_dict2,
            "view": "raw",
            "fallback_reason": "no_index_handle",
        }
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(payload_fallback))],
            is_error=False,
        )

    try:
        tree_raw: object = _build_directory_tree(
            workspace, path, 0, max_depth, exclude_patterns
        )
    except Exception as exc:
        raise ToolError(
            f"Failed to build directory tree for '{path}': {exc}"
        ) from exc
    tree: dict[str, object] = tree_raw if isinstance(tree_raw, dict) else {}
    tree["path"] = path

    # changed_only: filter to subtrees that contain at least one dirty
    # descendant. Files drop unless their full path is dirty; directories
    # are kept only when at least one descendant file matches, so the
    # caller can see the ancestor chain of a changed path. Mirror this
    # behaviour with the same dirty-path source as ``list_directory``.
    if changed_only:
        tree_dirty_source: object | None = handle_check_store
        co_dirty_obj: list[object] = []
        if tree_dirty_source is not None:
            peek_fn: object = getattr(
                tree_dirty_source, "peek_dirty_paths", None
            )
            if callable(peek_fn):
                peek_result: object = peek_fn()
                if isinstance(peek_result, list):
                    co_dirty_obj = peek_result
        dirty_set: set[str] = {str(p) for p in co_dirty_obj if isinstance(p, str)}

        def _annotate_paths(
            node: dict[str, object], parent_path: str
        ) -> dict[str, object]:
            name_obj: object = node.get("name", "")
            name = name_obj if isinstance(name_obj, str) else str(name_obj)
            node_path = (
                join_path(parent_path, name) if name else parent_path
            )
            node["path"] = node_path
            children_obj: object = node.get("children")
            if isinstance(children_obj, list):
                new_children = [
                    _annotate_paths(child, node_path)
                    for child in children_obj
                    if isinstance(child, dict)
                ]
                node["children"] = new_children
            return node

        def _filter_dirty(
            node: dict[str, object],
        ) -> dict[str, object] | None:
            node_type_obj: object = node.get("type")
            node_type = (
                node_type_obj
                if isinstance(node_type_obj, str)
                else str(node_type_obj)
            )
            node_path_obj: object = node.get("path", "")
            node_path = (
                node_path_obj
                if isinstance(node_path_obj, str)
                else str(node_path_obj)
            )
            if node_type == "file":
                return node if node_path in dirty_set else None
            children_obj2: object = node.get("children")
            children_list_obj2: list[object] = (
                list(children_obj2)
                if isinstance(children_obj2, list)
                else []
            )
            kept_children: list[dict[str, object]] = []
            for child in children_list_obj2:
                if isinstance(child, dict):
                    filtered_child = _filter_dirty(child)
                    if filtered_child is not None:
                        kept_children.append(filtered_child)
            if not kept_children:
                return None
            node["children"] = kept_children
            return node

        annotated = _annotate_paths(tree, path)
        filtered_root = _filter_dirty(annotated)
        if filtered_root is None:
            tree = {"name": "", "type": "dir", "path": path, "children": []}
        else:
            tree = filtered_root

    # Attach counts/symbols metadata from the explore index.
    counts_by_path: dict[str, dict[str, int]] = {}
    symbols_by_path: dict[str, list[dict[str, str]]] = {}
    raw_tree_store: object | None = handle.store
    if raw_tree_store is not None:
        tree_sym_iter_obj: object = getattr(
            raw_tree_store, "iter_symbols", lambda: []
        )()
        tree_sym_iterable: Iterable[object] = (
            tree_sym_iter_obj if hasattr(tree_sym_iter_obj, "__iter__") else []
        )
        for sym in tree_sym_iterable:
            sym_path_obj: object = getattr(sym, "path", "")
            sym_name_obj: object = getattr(sym, "name", "")
            sym_kind_obj: object = getattr(sym, "kind", "")
            sym_path = (
                sym_path_obj if isinstance(sym_path_obj, str) else str(sym_path_obj)
            )
            sym_name = (
                sym_name_obj if isinstance(sym_name_obj, str) else str(sym_name_obj)
            )
            sym_kind = (
                sym_kind_obj if isinstance(sym_kind_obj, str) else str(sym_kind_obj)
            )
            counts_by_path.setdefault(sym_path, {"symbols": 0})
            counts_by_path[sym_path]["symbols"] += 1
            if include_symbols or view == "outline":
                symbols_by_path.setdefault(sym_path, []).append(
                    {"name": sym_name, "kind": sym_kind}
                )

    def _decorate(
        node: dict[str, object], parent_path: str
    ) -> dict[str, object]:
        # AC-09: ensure every node has a usable ``path`` even if the
        # builder forgot one (e.g. tests build a tree ad-hoc) so the
        # counts/symbols lookup and changed_only filter are not
        # silently skipped. Pre-order traversal so children are
        # decorated BEFORE the ranked sort reads their counts.
        node_path_obj: object = node.get("path")
        node_path: str
        if isinstance(node_path_obj, str) and node_path_obj:
            node_path = node_path_obj
        else:
            name_obj: object = node.get("name", "")
            name_str: str = (
                name_obj if isinstance(name_obj, str) else str(name_obj)
            )
            node_path = (
                join_path(parent_path, name_str) if name_str else parent_path
            )
            node["path"] = node_path
        if include_counts or view in {"compact", "ranked"}:
            node["counts"] = counts_by_path.get(node_path, {"symbols": 0})
        if include_symbols or view == "outline":
            node["symbols"] = symbols_by_path.get(node_path, [])
        children_obj: object = node.get("children")
        if isinstance(children_obj, list):
            children_list_obj: list[object] = list(children_obj)
            children_list: list[dict[str, object]] = [
                c for c in children_list_obj if isinstance(c, dict)
            ]
            # Decorate first so ranked sort sees fresh counts.
            for child in children_list:
                _decorate(child, node_path)
            if view == "ranked":

                def _tree_rank_key(c: object) -> tuple[int, str]:
                    if not isinstance(c, dict):
                        return (0, "")
                    counts_obj: object = c.get("counts", {})
                    symbols_count = 0
                    if isinstance(counts_obj, dict):
                        count_val: object = counts_obj.get("symbols", 0)
                        if isinstance(count_val, int):
                            symbols_count = count_val
                    name_obj: object = c.get("name", "")
                    name = (
                        name_obj if isinstance(name_obj, str) else str(name_obj)
                    )
                    return (-symbols_count, name)

                children_list.sort(key=_tree_rank_key)
            if limit_children > 0:
                children_list = children_list[:limit_children]
            node["children"] = children_list
        return node

    decorated = _decorate(tree, "")
    is_stale: bool = False
    if raw_tree_store is not None:
        tree_dirty_obj: object = getattr(
            raw_tree_store, "peek_dirty_paths", lambda: []
        )()
        if isinstance(tree_dirty_obj, list):
            is_stale = bool(tree_dirty_obj)
        elif isinstance(tree_dirty_obj, bool):
            is_stale = tree_dirty_obj
        else:
            is_stale = bool(tree_dirty_obj)
    payload: dict[str, object] = {
        "tree": decorated,
        "view": view,
        "include_counts": include_counts,
        "include_symbols": include_symbols,
        "limit_children": limit_children,
        "changed_only": changed_only,
        "index_used": True,
        "is_stale": is_stale,
    }
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))], is_error=False
    )


def handle_search_files(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Search for files matching a glob pattern within a workspace directory.

    Indexed args:

    * ``ranked``: rank paths by deterministic index signals.
    * ``role`` in {source, test, docs, config, generated, any}.
    * ``contains_symbol``: filter to files that define or mention
      the named symbol via the indexed structure rows. The
      contract ships: absence of the symbol in the index reports
      ``+0 component:no_indexed_data`` reasons, not a deferred
      placeholder.
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

    # AC-04: every role accepted by the parameter validator (source,
    # test, docs, config, generated) actually narrows the result
    # instead of silently falling back to the unfiltered glob set.
    # The handler used to pass ``docs`` / ``config`` / ``generated``
    # through unchanged; the canonical taxonomy now narrows each.
    if role != "any":
        from ralph.mcp.explore.ranking import matches_role

        matches = [m for m in matches if matches_role(m, role)]  # m is str; matches_role(str, str) -> bool

    # Pull the explore handle once so every subsequent branch uses
    # the same store; ``None`` preserves the legacy live contract.
    from ralph.mcp.explore.dirty_paths import resolve_explore_index

    _search_handle = resolve_explore_index(session)
    if _search_handle is not None:
        _search_store: ExploreStore | None = cast(
            "ExploreStore | None", getattr(_search_handle, "store", None)
        )
    else:
        _search_store = None

    contains_symbol_note: str | None = None
    if contains_symbol is not None:
        if _search_store is None:
            # No index attached — keep the live glob results but
            # surface the explicit fallback note so callers see why
            # the filter did not narrow matches.
            contains_symbol_note = "no_explore_index_handle"
        else:
            text = str(contains_symbol)
            symbol_files: set[str] = set()
            if "." in text or "::" in text:
                rows: Sequence[SymbolRow] = _search_store.find_symbols(qualified_name=text)
            else:
                rows = _search_store.find_symbols(name=text)
            for row in rows:
                if row.path:
                    symbol_files.add(row.path)
            if not symbol_files:
                contains_symbol_note = "no_matching_symbol"
            else:
                matches = [m for m in matches if m in symbol_files]
                contains_symbol_note = None

    # changed_only: filter to paths that are dirty in the index queue
    # (the production lifecycle hook keeps this fresh). Without an
    # index handle we fall back to a live git_status call so the
    # caller still gets a useful answer.
    changed_only_note: str | None = None
    is_git_changed: bool = False
    if changed_only:
        if _search_store is not None:
            dirty_paths = list(_search_store.peek_dirty_paths())
            if dirty_paths:
                dirty_set = {str(d) for d in dirty_paths}
                matches = [m for m in matches if m in dirty_set]
            else:
                matches = []
        else:
            try:
                from ralph.mcp.tools.git_read import run_git_command_lenient

                git_result = run_git_command_lenient(
                    workspace, ["status", "--porcelain"]
                )
            except Exception:
                git_result = None
            stdout = (
                git_result.stdout.decode("utf-8", errors="replace")
                if git_result is not None
                else ""
            )
            returncode = git_result.returncode if git_result is not None else 1
            changed_set: set[str] = set()
            if returncode == 0 and isinstance(stdout, str):
                porcelain_prefix = 3
                for line in stdout.splitlines():
                    if not line:
                        continue
                    raw = line[porcelain_prefix:] if len(line) > porcelain_prefix else ""
                    if " -> " in raw:
                        raw = raw.split(" -> ", 1)[1]
                    raw = raw.strip().strip('"')
                    if raw:
                        changed_set.add(raw)
            matches = (
                [m for m in matches if m in changed_set] if changed_set else []
            )
            changed_only_note = "live_git_status"
            is_git_changed = bool(changed_set)

    # Ranking.
    score_reasons: list[dict[str, object]] = []
    if ranked:
        from ralph.mcp.explore.ranking import score_search_file, sort_ranked

        basename = pattern.split("/")[-1]
        # Phase 2 wiring: when an index is attached and the candidate
        # is currently dirty, mark it as git-changed so the ranking
        # bonuses kick in even without a real git diff.
        contains_symbol_str = (
            str(contains_symbol)
            if (_search_store is not None and contains_symbol is not None)
            else None
        )
        if _search_store is not None and not is_git_changed:
            dirty_paths_for_ranking = {
                str(d) for d in _search_store.peek_dirty_paths()
            }
            items = [
                score_search_file(
                    candidate_path=m,
                    basename=basename,
                    role_requested=role if role != "any" else None,
                    is_git_changed=(m in dirty_paths_for_ranking),
                    contains_symbol=contains_symbol_str,
                )
                for m in matches
            ]
        else:
            items = [
                score_search_file(
                    candidate_path=m,
                    basename=basename,
                    role_requested=role if role != "any" else None,
                    is_git_changed=is_git_changed,
                    contains_symbol=contains_symbol_str,
                )
                for m in matches
            ]
        items = sort_ranked(items)
        matches = [item.path for item in items]
        score_reasons = [item.to_dict() for item in items]

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
        "changed_only_note": changed_only_note,
    }
    if score_reasons:
        output["score_reasons"] = score_reasons
    if return_evidence_ids:
        # AC-02: emit only persisted evidence IDs that read_file can
        # resolve. Pull the file's stored content hash and insert
        # (or refresh) the evidence row keyed by the prompt's
        # deterministic evidence-id formula, so the caller can
        # read_file(evidence_id=...) and get the same path back.
        from ralph.mcp.explore.dirty_paths import resolve_explore_index
        from ralph.mcp.explore.store import (
            EvidenceRow,
            derive_evidence_id,
        )

        handle = resolve_explore_index(session)
        if handle is not None:
            store: ExploreStore | None = getattr(handle, "store", None)
            if store is not None:
                evidence_ids: list[str] = []
                now = time.time()
                for m in matches:
                    file_row = store.get_file(m)
                    if file_row is None:
                        evidence_ids.append(
                            derive_evidence_id(
                                path=m,
                                content_hash="",
                                start_line=0,
                                end_line=0,
                                kind="path",
                                extractor_version="phase1-lexical-v1",
                            )
                        )
                        continue
                    content_hash = file_row.content_hash
                    ev_id = derive_evidence_id(
                        path=m,
                        content_hash=content_hash,
                        start_line=0,
                        end_line=0,
                        kind="path",
                        extractor_version="phase1-lexical-v1",
                    )
                    store.insert_evidence(
                        EvidenceRow(
                            evidence_id=ev_id,
                            path=m,
                            start_line=0,
                            end_line=0,
                            content_hash=content_hash,
                            generation=file_row.indexed_generation,
                            source_tool="search_files",
                            evidence_kind="path",
                            created_at=now,
                            is_stale=False,
                        )
                    )
                    evidence_ids.append(ev_id)
                output["evidence_ids"] = evidence_ids
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(output))], is_error=False
    )
