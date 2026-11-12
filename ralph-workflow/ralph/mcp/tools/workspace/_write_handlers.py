from __future__ import annotations

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
from ralph.mcp.tools.text_edits import (
    MATCH_STRATEGIES,
    RejectedTextEdits,
    TextEditAnchor,
    apply_text_edits,
    parse_text_edits,
    sha256_text,
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
    handle = resolve_explore_index(session)
    if handle is None:
        return {}
    return _freshness_payload_from_handle(handle, paths=paths)


def _freshness_payload_from_handle(
    handle: object,
    *,
    paths: list[str],
) -> dict[str, object]:
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
    deleted_count = store.count_deleted_files()
    is_stale_value = bool(dirty) or deleted_count > 0
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
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)
    check_edit_area_restriction(session, normalized)
    require_capability(session, WORKSPACE_EDIT_CAPABILITY, "Workspace edit")
    edits = parse_text_edits(params)
    dry_run = bool(params.get("dry_run", False))
    expected_hash_raw = params.get("expected_content_hash")
    expected_hash: str | None = (
        str(expected_hash_raw) if isinstance(expected_hash_raw, str) else None
    )
    target_param = params.get("target")
    match_strategy = str(params.get("match_strategy", "exact"))
    if match_strategy not in MATCH_STRATEGIES:
        raise InvalidParamsError(
            f"Invalid match_strategy: {match_strategy!r}; expected "
            "'exact', 'within_target', or 'all_in_target'"
        )
    reindex_mode = str(params.get("reindex", "auto"))
    if reindex_mode not in {"auto", "skip", "changed_blocking"}:
        raise InvalidParamsError(
            f"Invalid reindex: {reindex_mode!r}; expected 'auto', 'skip', or 'changed_blocking'"
        )
    impact_preview = bool(params.get("impact_preview", False))
    return_evidence_updates = bool(params.get("return_evidence_updates", False))

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
                                    "file_missing" if actual_hash is None else "content_changed"
                                ),
                            }
                        )
                    )
                ],
                is_error=True,
            )

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
                    content=[ToolContent.text_content(_tool_json(target_resolution_error))],
                    is_error=True,
                )
            store: ExploreStore = cast("ExploreStore", store_obj)
            evidence_id = target_param.get("evidence_id")
            span_id = target_param.get("span_id")
            symbol_name = target_param.get("symbol")
            symbol_path = target_param.get("path")
            selector_count = sum(
                isinstance(value, str) and bool(value)
                for value in (evidence_id, span_id, symbol_name)
            )
            if selector_count != 1:
                target_resolution_error = {
                    "status": "ambiguous_target",
                    "reason": "target_requires_exactly_one_selector",
                    "target": target_param,
                }
            resolved = None
            resolved_path: str | None = None
            resolved_content_hash: str | None = None
            if target_resolution_error is None and isinstance(evidence_id, str) and evidence_id:
                row = store.get_evidence(evidence_id)
                if row is not None:
                    resolved = (row.start_line, row.end_line)
                    resolved_path = row.path
                    resolved_content_hash = row.content_hash
            elif target_resolution_error is None and isinstance(span_id, str) and span_id:
                span_row = next(
                    (s for s in store.iter_spans() if s.span_id == span_id),
                    None,
                )
                if span_row is not None:
                    resolved = (span_row.start_line, span_row.end_line)
                    resolved_path = span_row.path
                    resolved_content_hash = span_row.content_hash
            elif target_resolution_error is None and isinstance(symbol_name, str) and symbol_name:
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
                    span_row = next(
                        (s for s in store.iter_spans() if s.span_id == sym.span_id),
                        None,
                    )
                    if span_row is not None:
                        resolved = (span_row.start_line, span_row.end_line)
                        resolved_path = span_row.path
                        resolved_content_hash = span_row.content_hash
                elif len(scoped) > 1:
                    target_resolution_error = {
                        "status": "ambiguous_target",
                        "reason": "multiple_symbol_matches",
                        "matches": [m.qualified_name for m in scoped],
                        "target": target_param,
                    }
            if resolved is not None and resolved_path is not None and resolved_path != normalized:
                target_resolution_error = {
                    "status": "ambiguous_target",
                    "reason": "target_path_mismatch",
                    "target_path": resolved_path,
                    "edit_path": normalized,
                    "target": target_param,
                }
                resolved = None
            if resolved is None and target_resolution_error is None:
                target_resolution_error = {
                    "status": "ambiguous_target",
                    "reason": "target_unresolved",
                    "target": target_param,
                }
            target_span = resolved
            if (
                expected_hash is None
                and resolved is not None
                and resolved_path == normalized
                and resolved_content_hash
                and isinstance(resolved_content_hash, str)
            ):
                original_target_hash = _hash_file_text(workspace, normalized)
                if (
                    original_target_hash is not None
                    and original_target_hash != resolved_content_hash
                ):
                    target_resolution_error = {
                        "status": "stale_evidence",
                        "reason": "content_changed",
                        "path": normalized,
                        "target": target_param,
                        "resolved_content_hash": resolved_content_hash,
                        "current_content_hash": original_target_hash,
                    }
                    target_span = None

    if target_resolution_error is not None:
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(target_resolution_error))],
            is_error=True,
        )

    try:
        original_content = workspace.read(normalized)
    except FileNotFoundError:
        original_content = ""

    anchor = (
        TextEditAnchor(
            start_line=target_span[0], end_line=target_span[1], match_strategy=match_strategy
        )
        if target_span is not None
        else None
    )
    outcome = apply_text_edits(original_content, edits, label=path, anchor=anchor)
    if isinstance(outcome, RejectedTextEdits):
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(outcome.payload))],
            is_error=True,
        )

    current_content = outcome.content
    applied_edits = outcome.applied
    diff_text = outcome.diff

    if dry_run:
        preview_payload: dict[str, object] = {
            "status": "preview",
            "diff": diff_text,
            "edits_applied": len(applied_edits),
        }
        if impact_preview:
            handle_for_impact = resolve_explore_index(session)
            if handle_for_impact is None:
                preview_payload["impact_preview_unavailable"] = True
                preview_payload["impact_preview_unavailable_reason"] = "no_explore_index_handle"
                preview_payload["impact_preview"] = {
                    "available": False,
                    "reason": "no_explore_index_handle",
                }
            elif target_span is None:
                preview_payload["impact_preview_unavailable"] = True
                preview_payload["impact_preview_unavailable_reason"] = "no_symbol_target_for_impact"
                preview_payload["impact_preview"] = {
                    "available": False,
                    "reason": "no_symbol_target_for_impact",
                }
            else:
                try:
                    from ralph.mcp.explore._graph_query import run_query

                    impact_handle = handle_for_impact
                    impact_store_obj: ExploreStoreLike | None = (
                        impact_handle.store if impact_handle is not None else None
                    )
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
                        symbols = list(cast("ExploreStore", impact_store_obj).iter_symbols())
                        scoped_symbols = [
                            s for s in symbols if target_symbol_name in (s.name, s.qualified_name)
                        ]
                        if isinstance(target_path, str) and target_path:
                            scoped_symbols = [s for s in scoped_symbols if s.path == target_path]
                        if len(scoped_symbols) == 1:
                            target_symbol_id = scoped_symbols[0].symbol_id
                            if not target_path:
                                target_path = scoped_symbols[0].path
                    if impact_store_obj is not None and target_symbol_id is not None:
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
            Path(str(workspace_root_obj)) if isinstance(workspace_root_obj, (str, Path)) else None
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
                pass
    freshness = _freshness_payload(session, paths=[normalized])
    payload = _with_freshness(
        {
            "status": "applied",
            "diff": diff_text,
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
    try:
        content = workspace.read(normalized)
    except Exception:
        return None
    return sha256_text(content)


def handle_append_file(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
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
