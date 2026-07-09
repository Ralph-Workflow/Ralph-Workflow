"""Write, edit, append, create, move, copy, and delete handler functions."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.explore.dirty_paths import (
    mark_path,
    mark_paths,
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
    from ralph.mcp.explore.dirty_paths import ExploreIndexLike, ExploreStoreLike
    from ralph.mcp.explore.store import ExploreStore
    from ralph.workspace import Workspace


def _freshness_payload(
    session: CoordinationSessionLike,
    *,
    paths: list[str],
) -> dict[str, object]:
    """Return the freshness metadata block for a successful mutation.

    Returns an empty dict when the explore index is disabled so the
    existing tool output is unchanged.

    Field semantics (per the prompt's freshness contract):

    * ``index_used`` — True when an explore index handle exists.
    * ``index_generation`` — current index generation (``0`` when
      no reindex has happened).
    * ``is_stale`` — True when there are dirty paths OR deleted file
      rows in the index (agents should refresh before relying on it).
    * ``stale_paths_count`` — count of files marked deleted in the
      index (the path may no longer exist on disk).
    * ``reindex_in_progress`` — True when a reindex writer is active;
      MCP readers use the last committed generation in that case.
    * ``changed_paths`` — the workspace-relative paths this mutation
      just touched (NOT ``marked_paths`` — that name was misleading
      because the dirty path is independent of this call).
    """
    handle = resolve_explore_index(session)
    if handle is None:
        return {}
    return _freshness_payload_from_handle(handle, paths=paths)


def _freshness_payload_from_handle(
    handle: object,
    *,
    paths: list[str],
) -> dict[str, object]:
    """Internal: build the freshness payload when the handle is known.

    Split from :func:`_freshness_payload` so the type narrowing on
    ``handle.store`` is visible to mypy without an ``attr-defined``
    suppression at every call site.
    """
    typed_handle = cast("ExploreIndexLike | None", handle)
    if typed_handle is None:
        return {}
    store_obj: ExploreStoreLike | None = getattr(typed_handle, "store", None)
    if store_obj is None:
        return {}
    store: ExploreStore = cast("ExploreStore", store_obj)
    generation_raw = store.get_setting("current_generation") or "0"
    try:
        generation_int = int(generation_raw)
    except (TypeError, ValueError):
        generation_int = 0
    dirty = store.peek_dirty_paths()
    deleted_count = sum(1 for row in store.iter_files() if row.is_deleted)
    is_stale_value = bool(dirty) or deleted_count > 0
    # AC-04: reindex_in_progress is a typed optional attribute. Some
    # production handle types (older test doubles) do not expose it;
    # default to False rather than raising after a successful mutation.
    in_progress_attr: object = getattr(typed_handle, "reindex_in_progress", False)
    in_progress: bool = bool(in_progress_attr)
    return {
        "index_used": True,
        "index_generation": generation_int,
        "is_stale": is_stale_value,
        "dirty_paths_count": len(dirty),
        "stale_paths_count": deleted_count,
        "reindex_in_progress": in_progress,
        "changed_paths": [normalize_relative_path(p) for p in paths],
    }


def _with_freshness(
    payload: dict[str, object],
    freshness: dict[str, object],
) -> dict[str, object]:
    """Merge freshness metadata into an existing JSON payload dict."""
    if not freshness:
        return payload
    payload = dict(payload)
    payload.update(freshness)
    return payload


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
    handle = resolve_explore_index(session)
    mark_path(handle, path=normalized, source_tool="write_file")
    freshness = _freshness_payload(session, paths=[normalized])
    if freshness:
        # Indexed path returns a JSON envelope so the freshness block
        # has somewhere to live; the disabled path keeps the prior
        # plain-text success confirmation.
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        _with_freshness(
                            {
                                "path": path,
                                "bytes_written": len(content),
                                "status": "ok",
                            },
                            freshness,
                        )
                    )
                )
            ],
            is_error=False,
        )
    return ToolResult(
        content=[ToolContent.text_content(f"Successfully wrote {len(content)} bytes to {path}")],
        is_error=False,
    )


def handle_edit_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Apply structured oldText/newText replacements to a workspace file.

    AC-10 indexed args:

    * ``expected_content_hash`` -- fail closed when the file's
      current SHA-256 does not match.
    * ``target`` (``evidence_id`` / ``span_id`` / ``symbol``) --
      anchor the edit to an indexed span. Resolution happens after
      workspace path normalization; unknown symbols return a
      structured ``ambiguous_target`` error before any mutation.
    * ``match_strategy`` ``exact|within_target|all_in_target`` --
      constrains how the edit anchors inside the target span.
      ``exact`` requires the edit's oldText to equal the indexed
      span; ``within_target`` accepts an occurrence inside; the
      default is ``exact`` so callers can opt in.
    * ``reindex`` ``auto|skip|changed_blocking`` -- controls the
      dirty marking + blocking refresh behavior. ``auto`` marks
      the path dirty and lets the lifecycle handle it.
    * ``impact_preview`` -- only valid with ``dry_run=true``.
      Returns conservative graph impact when the explore index is
      available; otherwise returns ``impact_preview_unavailable``.
    * ``return_evidence_updates`` -- include the post-mutation
      generation + freshness in the response.
    """
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)
    check_edit_area_restriction(session, normalized)
    require_capability(session, WORKSPACE_EDIT_CAPABILITY, "Workspace edit")
    edits_param = params.get("edits")
    if not isinstance(edits_param, list) or len(edits_param) == 0:
        raise InvalidParamsError("Missing 'edits' parameter as non-empty list")
    edits = cast("list[dict[str, str]]", edits_param)
    dry_run = bool(params.get("dry_run", False))
    expected_hash_raw = params.get("expected_content_hash")
    expected_hash: str | None = (
        str(expected_hash_raw) if isinstance(expected_hash_raw, str) else None
    )
    target_param = params.get("target")
    match_strategy = str(params.get("match_strategy", "exact"))
    if match_strategy not in {"exact", "within_target", "all_in_target"}:
        raise InvalidParamsError(
            f"Invalid match_strategy: {match_strategy!r}; expected "
            "'exact', 'within_target', or 'all_in_target'"
        )
    reindex_mode = str(params.get("reindex", "auto"))
    if reindex_mode not in {"auto", "skip", "changed_blocking"}:
        raise InvalidParamsError(
            f"Invalid reindex: {reindex_mode!r}; expected "
            "'auto', 'skip', or 'changed_blocking'"
        )
    impact_preview = bool(params.get("impact_preview", False))
    return_evidence_updates = bool(params.get("return_evidence_updates", False))

    # Precondition: hash mismatch fails closed before any mutation.
    if expected_hash is not None:
        actual_hash = _hash_file_text(workspace, normalized)
        if actual_hash is None or actual_hash != expected_hash:
            return ToolResult(
                content=[
                    ToolContent.text_content(
                        _tool_json(
                            {
                                "status": "stale_evidence",
                                "path": path,
                                "expected_content_hash": expected_hash,
                                "current_content_hash": actual_hash,
                                "reason": (
                                    "file_missing"
                                    if actual_hash is None
                                    else "content_changed"
                                ),
                            }
                        )
                    )
                ],
                is_error=True,
            )

    # Target resolution. Evidence/spans/symbols come from the
    # explore index; symbol anchors require a path in addition to
    # the name so the resolution is unambiguous.
    target_span: tuple[int, int] | None = None
    target_resolution_error: dict[str, object] | None = None
    if isinstance(target_param, dict) and target_param:
        handle_for_target: ExploreIndexLike | None = resolve_explore_index(session)
        if handle_for_target is None:
            target_resolution_error = {
                "status": "ambiguous_target",
                "reason": "no_explore_index_handle",
                "target": target_param,
            }
        else:
            store_obj: ExploreStoreLike | None = handle_for_target.store
            if store_obj is None:
                target_resolution_error = {
                    "status": "ambiguous_target",
                    "reason": "no_explore_index_handle",
                    "target": target_param,
                }
                return ToolResult(
                    content=[
                        ToolContent.text_content(
                            _tool_json(target_resolution_error)
                        )
                    ],
                    is_error=True,
                )
            store: ExploreStore = cast("ExploreStore", store_obj)
            evidence_id = target_param.get("evidence_id")
            span_id = target_param.get("span_id")
            symbol_name = target_param.get("symbol")
            symbol_path = target_param.get("path")
            resolved = None
            if isinstance(evidence_id, str) and evidence_id:
                row = store.get_evidence(evidence_id)
                if row is not None:
                    resolved = (row.start_line, row.end_line)
            elif isinstance(span_id, str) and span_id:
                span_row = next(
                    (
                        s
                        for s in store.iter_spans()
                        if s.span_id == span_id
                    ),
                    None,
                )
                if span_row is not None:
                    resolved = (span_row.start_line, span_row.end_line)
            elif isinstance(symbol_name, str) and symbol_name:
                # Symbol lookup is path-scoped when path is given;
                # otherwise fall back to ambiguous_target if the
                # symbol appears in more than one file.
                matches = [
                    sym
                    for sym in store.iter_symbols()
                    if symbol_name in (sym.name, sym.qualified_name)
                ]
                scoped = (
                    [m for m in matches if m.path == symbol_path]
                    if isinstance(symbol_path, str) and symbol_path
                    else matches
                )
                if len(scoped) == 1:
                    sym = scoped[0]
                    # Symbol stores span_id; resolve span via iter_spans.
                    span_row = next(
                        (
                            s
                            for s in store.iter_spans()
                            if s.span_id == sym.span_id
                        ),
                        None,
                    )
                    if span_row is not None:
                        resolved = (span_row.start_line, span_row.end_line)
                elif len(scoped) > 1:
                    target_resolution_error = {
                        "status": "ambiguous_target",
                        "reason": "multiple_symbol_matches",
                        "matches": [m.qualified_name for m in scoped],
                        "target": target_param,
                    }
            if resolved is None and target_resolution_error is None:
                target_resolution_error = {
                    "status": "ambiguous_target",
                    "reason": "target_unresolved",
                    "target": target_param,
                }
            target_span = resolved

    if target_resolution_error is not None:
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(target_resolution_error))],
            is_error=True,
        )

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
        # Target anchoring: convert the line offset to byte offset.
        if target_span is not None:
            line_start, line_end = target_span
            anchor_offset, anchor_end = _line_range_to_byte_offsets(
                current_content, line_start, line_end
            )
            if match_strategy == "exact":
                if idx != anchor_offset or (idx + len(old_text)) > anchor_end:
                    return ToolResult(
                        content=[
                            ToolContent.text_content(
                                _tool_json(
                                    {
                                        "status": "ambiguous_target",
                                        "reason": "match_strategy_exact_violation",
                                        "edit_index": i,
                                    }
                                )
                            )
                        ],
                        is_error=True,
                    )
            elif match_strategy == "within_target":
                if idx < anchor_offset or (idx + len(old_text)) > anchor_end:
                    return ToolResult(
                        content=[
                            ToolContent.text_content(
                                _tool_json(
                                    {
                                        "status": "ambiguous_target",
                                        "reason": "match_strategy_within_target_violation",
                                        "edit_index": i,
                                    }
                                )
                            )
                        ],
                        is_error=True,
                    )
            elif (
                match_strategy == "all_in_target"
                and (idx < anchor_offset or (idx + len(new_text)) > anchor_end)
            ):
                    return ToolResult(
                        content=[
                            ToolContent.text_content(
                                _tool_json(
                                    {
                                        "status": "ambiguous_target",
                                        "reason": "match_strategy_all_in_target_violation",
                                        "edit_index": i,
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
        preview_payload: dict[str, object] = {
            "status": "preview",
            "diff": "".join(diff),
            "edits_applied": len(applied_edits),
        }
        if impact_preview:
            handle_for_impact = resolve_explore_index(session)
            if handle_for_impact is None:
                # AC-10: surface the plan-described
                # ``impact_preview_unavailable`` field alongside the
                # existing diff so the caller can distinguish "no
                # index" from "index present, no symbol target".
                preview_payload["impact_preview_unavailable"] = True
                preview_payload["impact_preview_unavailable_reason"] = (
                    "no_explore_index_handle"
                )
                preview_payload["impact_preview"] = {
                    "available": False,
                    "reason": "no_explore_index_handle",
                }
            elif target_span is None:
                preview_payload["impact_preview_unavailable"] = True
                preview_payload["impact_preview_unavailable_reason"] = (
                    "no_symbol_target_for_impact"
                )
                preview_payload["impact_preview"] = {
                    "available": False,
                    "reason": "no_symbol_target_for_impact",
                }
            else:
                # AC-10: when a symbol target is available AND the
                # explore index is attached, run the conservative
                # ``impact`` graph query and surface callers,
                # importers, and suggested tests. Dynamic / reflection
                # / unsupported relations are marked as ``unknown``
                # by the graph module.
                try:
                    from ralph.mcp.explore.graph import run_query

                    impact_handle = handle_for_impact
                    impact_store_obj: ExploreStoreLike | None = (
                        impact_handle.store
                        if impact_handle is not None
                        else None
                    )
                    # ``target_span`` here is a (line_start, line_end)
                    # tuple resolved earlier; the actual symbol id /
                    # path live on the originating ``target_param`` and
                    # the indexed symbol/span rows.
                    target_param_dict: dict[str, object] = (
                        target_param if isinstance(target_param, dict) else {}
                    )
                    target_symbol_name = target_param_dict.get("symbol")
                    target_path = target_param_dict.get("path")
                    target_symbol_id: str | None = None
                    if (
                        impact_store_obj is not None
                        and isinstance(target_symbol_name, str)
                        and target_symbol_name
                    ):
                        symbols = list(
                            cast("ExploreStore", impact_store_obj).iter_symbols()
                        )
                        scoped_symbols = [
                            s
                            for s in symbols
                            if target_symbol_name in (s.name, s.qualified_name)
                        ]
                        if (
                            isinstance(target_path, str)
                            and target_path
                        ):
                            scoped_symbols = [
                                s
                                for s in scoped_symbols
                                if s.path == target_path
                            ]
                        if len(scoped_symbols) == 1:
                            target_symbol_id = scoped_symbols[0].symbol_id
                            if not target_path:
                                target_path = scoped_symbols[0].path
                    if (
                        impact_store_obj is not None
                        and target_symbol_id is not None
                    ):
                        result = run_query(
                            cast("ExploreStore", impact_store_obj),
                            query_type="impact",
                            target=target_symbol_id,
                            change_kind="behavior",
                            limit=25,
                            freshness="prefer_fresh",
                        )
                        preview_payload["impact_preview"] = {
                            "available": True,
                            "impacted_files": list(result.impacted_files),
                            "suggested_tests": [
                                {
                                    "path": n.path,
                                    "name": n.label,
                                    "kind": n.kind,
                                }
                                for n in result.suggested_tests
                            ],
                            "missing_data": list(result.missing_data),
                            "is_stale": result.is_stale,
                            "index_generation": result.index_generation,
                        }
                    else:
                        preview_payload["impact_preview_unavailable"] = True
                        preview_payload["impact_preview_unavailable_reason"] = (
                            "no_symbol_id_for_impact"
                        )
                        preview_payload["impact_preview"] = {
                            "available": False,
                            "reason": "no_symbol_id_for_impact",
                        }
                    if target_path is not None:
                        preview_payload["impact_preview_path"] = str(target_path)
                except Exception as exc:
                    preview_payload["impact_preview_unavailable"] = True
                    preview_payload["impact_preview_unavailable_reason"] = (
                        f"impact_query_failed:{type(exc).__name__}"
                    )
                    preview_payload["impact_preview"] = {
                        "available": False,
                        "reason": f"impact_query_failed:{type(exc).__name__}",
                    }
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(preview_payload))],
            is_error=False,
        )

    try:
        workspace.write(normalized, current_content)
    except Exception as exc:
        raise ToolError(f"Failed to write file '{path}': {exc}") from exc
    handle = resolve_explore_index(session)
    mark_path(handle, path=normalized, source_tool="edit_file")
    if reindex_mode == "changed_blocking":
        typed_handle: ExploreIndexLike | None = handle
        edit_store_obj: ExploreStoreLike | None = (
            typed_handle.store if typed_handle is not None else None
        )
        workspace_root_obj: object = getattr(workspace, "root", None)
        workspace_root_path: Path | None = (
            Path(str(workspace_root_obj))
            if isinstance(workspace_root_obj, (str, Path))
            else None
        )
        if edit_store_obj is not None and workspace_root_path is not None:
            try:
                from ralph.mcp.explore.pipeline import (
                    DEFAULT_TIMEOUT_MS,
                    ReindexOptions,
                    reindex,
                )
                reindex(
                    cast("ExploreStore", edit_store_obj),
                    workspace_root_path,
                    options=ReindexOptions(
                        mode="changed",
                        timeout_ms=DEFAULT_TIMEOUT_MS,
                        path_scope=(normalized,),
                    ),
                )
            except Exception:
                # Fail-open: do not let a reindex failure fail the edit.
                pass
    freshness = _freshness_payload(session, paths=[normalized])
    payload = _with_freshness(
        {
            "status": "applied",
            "diff": "".join(diff),
            "bytes_written": len(current_content),
        },
        freshness,
    )
    if return_evidence_updates:
        payload["evidence_updates"] = {
            "dirty_path": normalized,
            "index_generation": freshness.get("index_generation", 0),
            "reindex_in_progress": freshness.get("reindex_in_progress", False),
            "is_stale": freshness.get("is_stale", False),
        }
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=False,
    )


def _hash_file_text(workspace: Workspace, normalized: str) -> str | None:
    """Return the SHA-256 hex digest of the file's current bytes."""
    import hashlib

    try:
        content = workspace.read(normalized)
    except Exception:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _line_range_to_byte_offsets(
    text: str, start_line: int, end_line: int
) -> tuple[int, int]:
    """Convert ``(start_line, end_line)`` (1-based, inclusive) to byte offsets.

    The mapping is conservative: missing line boundaries fall back
    to ``0`` and ``len(text)`` so the byte-offset checks in
    ``match_strategy`` never crash on edge cases.
    """
    if not text:
        return 0, 0
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0, 0
    start_index = max(0, min(len(lines), start_line - 1))
    end_index = max(start_index, min(len(lines), end_line))
    start_offset = sum(len(line) for line in lines[:start_index])
    end_offset = sum(len(line) for line in lines[:end_index])
    return start_offset, end_offset


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
    handle = resolve_explore_index(session)
    mark_path(handle, path=normalized, source_tool="append_file")
    freshness = _freshness_payload(session, paths=[normalized])
    payload = _with_freshness(
        {"path": path, "bytes_appended": len(content)},
        freshness,
    )
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
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
    handle = resolve_explore_index(session)
    mark_path(handle, path=normalized, source_tool="create_directory")
    freshness = _freshness_payload(session, paths=[normalized])
    payload = _with_freshness({"path": path, "created": True}, freshness)
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
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
    handle = resolve_explore_index(session)
    mark_paths(handle, paths=[src_norm, dest_norm], source_tool="move_file")
    freshness = _freshness_payload(session, paths=[src_norm, dest_norm])
    payload = _with_freshness({"src": src, "dest": dest}, freshness)
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
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
    handle = resolve_explore_index(session)
    mark_path(handle, path=dest_norm, source_tool="copy_file")
    freshness = _freshness_payload(session, paths=[dest_norm])
    payload = _with_freshness({"src": src, "dest": dest}, freshness)
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
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
    handle = resolve_explore_index(session)
    mark_path(handle, path=normalized, source_tool="delete_path")
    freshness = _freshness_payload(session, paths=[normalized])
    payload = _with_freshness(
        {"path": path, "deleted": True, "recursive": recursive},
        freshness,
    )
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=False,
    )
