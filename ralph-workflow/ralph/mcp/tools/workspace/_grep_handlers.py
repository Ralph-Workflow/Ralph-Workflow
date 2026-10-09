"""Grep/content-search handler."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.explore.dirty_paths import resolve_explore_index
from ralph.mcp.explore.ranking import (
    INDEXED_COMPONENT_NOT_AVAILABLE,
    fts_query_for,
    is_fts_eligible,
    score_grep_match,
    sort_ranked,
)
from ralph.mcp.explore.serving import serving_metadata, staleness_probe
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.workspace._grep_evidence import (
    _derive_evidence_id_for_span,
    _EvidenceRowBuilder,
)
from ralph.mcp.tools.workspace._list_ops import (
    _collect_files_recursive,
    match_glob,
)
from ralph.mcp.tools.workspace._utils import (
    _GREP_DEFAULT_LIMIT,
    _MAX_PATTERN_LENGTH,
    WORKSPACE_READ_CAPABILITY,
    _int_param,
    _tool_json,
    normalize_relative_path,
    required_string_param,
)

if TYPE_CHECKING:
    from ralph.mcp.explore.store import EvidenceRow, ExploreStore
    from ralph.workspace import Workspace

# --- Index metadata helpers -----------------------------------------------


def _freshness_for_grep(
    session: object,
    *,
    index_used: bool,
    fallback_reason: str | None = None,
) -> dict[str, object]:
    """Return the freshness metadata block for a grep response.

    Returns the canonical serving-metadata block so the response
    carries ``index_used``, ``fallback_reason``, and
    ``index_staleness`` regardless of whether the index is enabled.
    The legacy keys (``index_generation``, ``is_stale``,
    ``dirty_paths_count``, ``stale_paths_count``) are preserved
    alongside the canonical ones so existing callers and tests
    keep working.
    """
    meta = serving_metadata(session, index_used=index_used, fallback_reason=fallback_reason)
    handle = resolve_explore_index(session)
    if handle is None:
        return {
            **meta,
            "index_generation": 0,
            "is_stale": False,
            "dirty_paths_count": 0,
            "stale_paths_count": 0,
        }
    store: ExploreStore | None = getattr(handle, "store", None)
    if store is None:
        return {
            **meta,
            "index_generation": 0,
            "is_stale": False,
            "dirty_paths_count": 0,
            "stale_paths_count": 0,
        }
    try:
        generation_raw = store.get_setting("current_generation") or "0"
    except (sqlite3.ProgrammingError, sqlite3.DatabaseError, AttributeError):
        # F5/F18: store is closed / corrupted. Return zeroed metadata
        # so the response is still meaningful for the live path.
        return {
            **meta,
            "index_generation": 0,
            "is_stale": False,
            "dirty_paths_count": 0,
            "stale_paths_count": 0,
        }
    try:
        generation_int = int(generation_raw)
    except (TypeError, ValueError):
        generation_int = 0
    try:
        dirty = list(store.peek_dirty_paths())
    except Exception:
        dirty = []
    return {
        **meta,
        "index_generation": generation_int,
        "is_stale": bool(dirty),
        "dirty_paths_count": len(dirty),
        "stale_paths_count": len(dirty),
    }


def _queue_recovery(workspace_root: Path | None, reason: str | None) -> None:
    """Queue background recovery for a detected index fault.

    Ineligible patterns and a missing session handle are not index
    damage, so they do not schedule a rebuild. A locked database
    means another writer already owns recovery (F7).
    """
    if workspace_root is None or reason is None:
        return
    if reason in {"pattern_not_fts_eligible", "no_index_handle", "index_locked"}:
        return
    from ralph.mcp.explore.recovery import enqueue_recovery

    enqueue_recovery(workspace_root, reason, message=reason)


def _cold_query_reason(store: ExploreStore) -> str:
    """Distinguish a never-built index from one whose last job timed out."""
    try:
        latest = store.latest_job()
    except (sqlite3.ProgrammingError, sqlite3.DatabaseError, AttributeError, TypeError):
        return "no_committed_generation"
    if latest is None:
        return "no_committed_generation"
    try:
        status_cell: object = latest["status"]
        status = str(status_cell)
    except (KeyError, IndexError, TypeError):
        return "no_committed_generation"
    if status == "timed_out":
        return "timeout_exceeded"
    return "no_committed_generation"


def _indexed_committed_generation(store: ExploreStore | None) -> int:
    """Return the current committed generation, or 0 when the store has none.

    Used by the grep handler to detect cold stores (current_generation
    == 0 because no reindex has committed) so we fall back to live
    grep instead of returning an empty result from a never-populated
    index.
    """
    if store is None:
        return 0
    try:
        raw = store.get_setting("current_generation")
    except Exception:
        return 0
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 0


def _indexed_matches(
    store: ExploreStore,
    pattern: str,
    *,
    whole_word: bool,
    case_sensitive: bool,
    limit: int,
    path_prefix: str | None = None,
    include_globs: Sequence[str] | None = None,
    exclude_globs: Sequence[str] | None = None,
    overscan_multiplier: int = 8,
) -> list[dict[str, object]]:
    """Run an FTS5 search and translate rows to the live match shape.

    Each returned ``evidence_id`` is a real row in the ``evidence``
    table so ``read_file(evidence_id=...)`` resolves to the exact
    span instead of returning ``unknown_evidence``. The translation
    looks up the chunk's stored line range and content hash via a
    single ``fts_search_grep`` JOIN (path, chunk_id, full text,
    start_line, end_line, text_hash, generation, file_content_hash)
    so the candidate loop never issues per-candidate SELECTs against
    ``chunks_fts.text`` or ``chunks.start_line`` -- those lookups
    were the R6.4 indexed-vs-live latency regression on large
    corpora. Evidence rows are then inserted in one
    ``insert_evidence_batch`` call instead of one ``insert_evidence``
    per match, so a 100-match response issues a bounded 2 SQL
    round-trips regardless of result-set size.

    AC-01 case-sensitive post-filter: FTS5 ``unicode61`` is
    case-INsensitive, so we re-compile the literal as a
    case-sensitive regex and filter the FTS candidates against it.
    This keeps result sets identical to live grep for case-sensitive
    queries while still benefiting from FTS5 narrowing.

    AC-02 indexed-grep filter parity: ``path_prefix``,
    ``include_globs``, and ``exclude_globs`` push the legacy
    grep filters into the indexed query so out-of-scope matches
    cannot leak into the indexed branch.

    The ``overscan_multiplier`` widens the FTS5 query so the
    post-filter still yields ``limit`` matches even when many FTS
    candidates fail the case-exact test (FTS5 limit caps results
    before the post-filter runs).

    The post-filter runs against the *full* chunk text (read from
    the JOINed ``chunks_fts.text`` column), not the truncated FTS5
    ``snippet()`` output, so multi-line chunks whose matching line
    falls outside the snippet window still match correctly.
    """
    fts_query = fts_query_for(pattern, whole_word=whole_word)
    # Case-sensitive parity demands that the FTS query return every
    # candidate the post-filter could accept. BM25 ordering + a hard
    # FTS5 limit would silently drop lower-ranked case-exact hits
    # before the post-filter sees them, breaking the parity
    # contract. We overscan by a generous factor to keep parity
    # while still bounding memory for pathological queries.
    fts_limit = max(limit, 1) * max(overscan_multiplier, 1)
    raw_rows = store.fts_search_grep(
        fts_query,
        limit=fts_limit,
        path_prefix=path_prefix,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
    )
    rows: list[sqlite3.Row] = list(raw_rows)
    post_filter = _compile_grep_pattern(
        pattern,
        is_regex=False,
        case_sensitive=case_sensitive,
        whole_word=whole_word,
    )
    matches: list[dict[str, object]] = []
    evidence_rows: list[EvidenceRow] = []
    for row in rows:
        raw_path_value: object = row["path"]
        raw_chunk_id_value: object = row["chunk_id"]
        path_str = str(raw_path_value) if raw_path_value is not None else ""
        chunk_id_str = str(raw_chunk_id_value) if raw_chunk_id_value is not None else ""
        # The single JOIN carries everything we need for the
        # post-filter and the evidence row. Per-row fault tolerance
        # is preserved: a missing field is a malformed candidate
        # (would be a corruption), so the whole row is skipped.
        try:
            full_text_obj: object = row["text"]
        except (IndexError, KeyError):
            full_text_obj = ""
        try:
            chunk_start_line_obj: object = row["start_line"]
        except (IndexError, KeyError):
            chunk_start_line_obj = 0
        try:
            text_hash_obj: object = row["text_hash"]
        except (IndexError, KeyError):
            text_hash_obj = ""
        try:
            generation_obj: object = row["generation"]
        except (IndexError, KeyError):
            generation_obj = 0
        try:
            file_content_hash_obj: object = row["file_content_hash"]
        except (IndexError, KeyError):
            file_content_hash_obj = ""
        full_text = str(full_text_obj) if full_text_obj is not None else ""
        if not full_text:
            continue
        chunk_start_line = int(chunk_start_line_obj) if isinstance(chunk_start_line_obj, int) else 0
        text_hash = str(text_hash_obj) if text_hash_obj is not None else ""
        generation = int(generation_obj) if isinstance(generation_obj, int) else 0
        # The content_hash for the evidence span is the file's
        # SHA-256 (the canonical file content_hash from the
        # ``files`` table). Fall back to the chunk's text_hash
        # when the JOIN's file row is missing or has no hash --
        # a defensive no-op since the JOIN filters out
        # ``is_deleted=1`` rows already.
        file_content_hash = str(file_content_hash_obj) if file_content_hash_obj is not None else ""
        content_hash = file_content_hash or text_hash
        # Per-line parity: find every line inside the chunk that
        # matches the regex so the indexed branch emits the same
        # (path, line) pairs the live branch emits, not just one
        # entry per chunk. ``chunk_start_line`` offsets the
        # in-chunk line index to the file's line numbers.
        in_chunk_line = 0
        for line_text in full_text.splitlines(keepends=False):
            in_chunk_line += 1
            if not post_filter.search(line_text):
                continue
            file_line = chunk_start_line + in_chunk_line - 1 if chunk_start_line else in_chunk_line
            evidence_id = _derive_evidence_id_for_span(
                path=path_str,
                content_hash=content_hash,
                start_line=file_line,
                end_line=file_line,
                kind="chunk_line",
            )
            match_row: dict[str, object] = {
                "path": path_str,
                "line": file_line,
                "text": line_text,
                "evidence_id": evidence_id,
                "chunk_id": chunk_id_str,
            }
            evidence_row = _EvidenceRowBuilder(
                evidence_id=evidence_id,
                path=path_str,
                start_line=file_line,
                end_line=file_line,
                content_hash=content_hash,
                generation=generation,
                source_tool="grep_files",
                evidence_kind="chunk_line",
            ).build()
            matches.append(match_row)
            evidence_rows.append(evidence_row)
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    if evidence_rows:
        # One ``executemany`` in a single transaction for every
        # match: bounded 2 SQL round-trips for the whole call
        # (the JOIN above + the batch insert) instead of one
        # round-trip per candidate and per match.
        store.insert_evidence_batch(evidence_rows)
    return matches


# --- Live grep helpers (preserved) ---------------------------------------


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
    _max_file_bytes: int,
) -> list[dict[str, object]] | None:
    """Search a single file for matches; returns None if the file should be skipped."""
    try:
        file_stat = workspace.stat(file_path)
    except Exception:
        return None

    if file_stat.get("type") == "dir":
        return None
    size_bytes = file_stat.get("size_bytes", 0)
    if isinstance(size_bytes, int) and size_bytes > _max_file_bytes:
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


def _live_grep(
    workspace: Workspace,
    *,
    pattern: str,
    path: str,
    normalized: str,
    is_regex: bool,
    case_sensitive: bool,
    whole_word: bool,
    include: object,
    exclude: object,
    context_before: int,
    context_after: int,
    limit: int,
    max_file_bytes: int,
) -> tuple[list[dict[str, object]], int, bool]:
    """Run the existing live grep pipeline; returns (matches, skipped, truncated)."""
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
    include_list: list[object] = list(cast("Iterable[object]", include)) if include else []
    exclude_list: list[object] = list(cast("Iterable[object]", exclude)) if exclude else []
    for file_path in all_files:
        if include_list and not any(match_glob(file_path, str(p)) for p in include_list):
            continue
        if exclude_list and any(match_glob(file_path, str(p)) for p in exclude_list):
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
    return matches, skipped_files, truncated


# --- Main handler ---------------------------------------------------------


def handle_grep_files(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
) -> ToolResult:
    """Search file contents for a pattern and return line-level matches."""
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Content search")
    pattern = required_string_param(params, "pattern")
    path = required_string_param(params, "path")
    normalized = normalize_relative_path(path)

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

    # Phase 1 indexed args.
    use_index = str(params.get("use_index", "auto"))
    if use_index not in {"auto", "always", "never"}:
        raise InvalidParamsError(
            f"Invalid use_index: {use_index!r}; expected 'auto', 'always', or 'never'"
        )
    rank_by = str(params.get("rank_by", "match"))
    if rank_by not in {"match", "symbol", "graph", "changed", "hybrid"}:
        raise InvalidParamsError(
            f"Invalid rank_by: {rank_by!r}; expected 'match', 'symbol', "
            "'graph', 'changed', or 'hybrid'"
        )
    return_evidence_ids = bool(params.get("return_evidence_ids", False))
    max_snippet_lines = _int_param(params, "max_snippet_lines", 8)
    dedupe_by_symbol = bool(params.get("dedupe_by_symbol", False))
    include_graph_context = bool(params.get("include_graph_context", False))

    handle = resolve_explore_index(session)
    if handle is not None:
        try:
            store_value: ExploreStore | None = getattr(handle, "store", None)
        except (sqlite3.ProgrammingError, sqlite3.DatabaseError):
            store_value = None
    else:
        store_value = None
    store: ExploreStore | None = store_value

    # Determine if FTS is eligible. Case-sensitive literals are now
    # eligible: the handler narrows candidates via FTS and
    # re-applies a case-sensitive regex post-filter so the result
    # set equals the live grep path's.
    eligible = is_fts_eligible(
        pattern,
        is_regex=is_regex,
        whole_word=whole_word,
        case_sensitive=case_sensitive,
    )
    index_used = False
    fallback_reason: str | None = None
    from ralph.mcp.explore.ranking import RankedItem

    ranked_items: list[RankedItem] = []
    indexed_match_rows: list[dict[str, object]] = []
    graph_context: list[dict[str, object]] = []

    # AC-01 cold-index guard: a never-reindexed store carries
    # ``current_generation == 0`` and would silently return 0
    # matches. Surface the missing data via fallback_reason and
    # fall back to live grep so the response still contains
    # matches. The committed-generation check overrides the
    # pattern-eligibility reason because the absence of an index
    # is the more fundamental block.
    cold_index = False
    if store is not None:
        try:
            cold_index = _indexed_committed_generation(store) <= 0
        except (sqlite3.ProgrammingError, sqlite3.DatabaseError):
            store = None
            cold_index = False
    workspace_raw: object = getattr(workspace, "root", None)
    workspace_root: Path | None = workspace_raw if isinstance(workspace_raw, Path) else None
    if cold_index and store is not None:
        fallback_reason = _cold_query_reason(store)
        if use_index == "always":
            _queue_recovery(workspace_root, fallback_reason)
            raise InvalidParamsError(
                f"use_index='always' cannot serve this query: reason_code={fallback_reason}"
            )
        eligible = False

    # S-3: pre-query freshness guard. When the index is stale past the
    # documented threshold (or any stale path falls inside the query
    # scope), auto mode falls through to live grep with reason
    # ``index_stale_scope``. ``use_index='always'`` fails closed with
    # that same reason code.
    probe = staleness_probe(
        session,
        workspace_root=workspace_root,
    )
    if probe["stale"]:
        eligible = False
        fallback_reason = "index_stale_scope"

    if use_index != "never" and store is not None and eligible:
        # AC-02 indexed-grep filter parity: push path/include/exclude
        # into the FTS query so the indexed branch never leaks
        # out-of-scope matches.
        try:
            # F7: fail fast when another connection holds the reserved lock.
            previous_busy = store._busy_timeout_ms
            store._conn.execute("PRAGMA busy_timeout=50")
            try:
                store._conn.execute("BEGIN IMMEDIATE")
                store._conn.execute("ROLLBACK")
            finally:
                store._conn.execute(f"PRAGMA busy_timeout={previous_busy}")
            indexed_match_rows = _indexed_matches(
                store,
                pattern,
                whole_word=whole_word,
                case_sensitive=case_sensitive,
                limit=limit,
                path_prefix=normalized or None,
                include_globs=include,
                exclude_globs=exclude,
            )
        except sqlite3.OperationalError as exc:
            low = str(exc).lower()
            fallback_reason = (
                "index_locked" if ("locked" in low or "busy" in low) else "indexer_error"
            )
            if use_index == "always":
                _queue_recovery(workspace_root, fallback_reason)
                raise InvalidParamsError(
                    f"use_index='always' cannot serve this query: reason_code={fallback_reason}"
                ) from exc
            live_matches, skipped, truncated = _live_grep(
                workspace,
                pattern=pattern,
                path=path,
                normalized=normalized,
                is_regex=is_regex,
                case_sensitive=case_sensitive,
                whole_word=whole_word,
                include=include,
                exclude=exclude,
                context_before=context_before,
                context_after=context_after,
                limit=limit,
                max_file_bytes=max_file_bytes,
            )
            _queue_recovery(workspace_root, fallback_reason)
            locked_result: dict[str, object] = {
                "pattern": pattern,
                "base": path,
                "matches": live_matches,
                "truncated": truncated,
                "skipped_files": skipped,
                "ranked_by": rank_by,
                "dedupe_by_symbol": dedupe_by_symbol,
                "graph_context": (
                    []
                    if include_graph_context
                    else f"graph_context:{INDEXED_COMPONENT_NOT_AVAILABLE}"
                ),
            }
            locked_result.update(
                _freshness_for_grep(session, index_used=False, fallback_reason=fallback_reason)
            )
            return ToolResult(
                content=[ToolContent.text_content(_tool_json(locked_result))],
                is_error=False,
            )
        index_used = True
        # Snippet cap.
        if max_snippet_lines and max_snippet_lines > 0:
            for row in indexed_match_rows:
                text = row.get("text") or ""
                if isinstance(text, str):
                    row["text"] = "\n".join(text.splitlines()[:max_snippet_lines])
        # Dedupe by symbol: collapses hits from the same chunk.
        if dedupe_by_symbol:
            seen_chunks: set[str] = set()
            deduped: list[dict[str, object]] = []
            for row in indexed_match_rows:
                key = str(row.get("evidence_id", row.get("path", "")))
                if key in seen_chunks:
                    continue
                seen_chunks.add(key)
                deduped.append(row)
            indexed_match_rows = deduped
        # Ranking.
        if rank_by != "match":
            for row in indexed_match_rows:
                path_raw: object = row.get("path", "")
                line_raw: object = row.get("line") or 0
                ev_raw: object = row.get("evidence_id", "")
                path_v = str(path_raw) if path_raw is not None else ""
                line_v: int
                if isinstance(line_raw, int) and not isinstance(line_raw, bool):
                    line_v = line_raw
                elif isinstance(line_raw, str):
                    try:
                        line_v = int(line_raw)
                    except ValueError:
                        line_v = 0
                else:
                    line_v = 0
                ev = str(ev_raw) if ev_raw is not None else ""
                # Phase 2 wiring: pass the store/chunk_id/graph_target
                # so the rank_by symbol/graph components can contribute
                # when the index has structure rows. Phase 1 callers
                # pass nothing and the lookup returns zero bonuses.
                ranked_items.append(
                    score_grep_match(
                        path=path_v,
                        line=line_v,
                        evidence_id=ev,
                        store=store,
                        chunk_id=str(row.get("chunk_id", "")) or None,
                        graph_target=(
                            str(params.get("graph_target")) if params.get("graph_target") else None
                        ),
                    )
                )
            ranked_items = sort_ranked(ranked_items)
            # Apply the same order to the match rows.
            order = {item.key: idx for idx, item in enumerate(ranked_items)}

            def _indexed_order(row: dict[str, object]) -> int:
                line_obj = row.get("line")
                line_key = line_obj if isinstance(line_obj, int) else 0
                key = f"{row.get('path', '')}:{line_key}:{row.get('evidence_id', '')}"
                return order.get(key, len(order))

            indexed_match_rows.sort(key=_indexed_order)
        if include_graph_context:
            for row in indexed_match_rows[:limit]:
                evidence_id = row.get("evidence_id")
                if not isinstance(evidence_id, str):
                    continue
                evidence = store.get_evidence(evidence_id)
                if evidence is not None:
                    graph_context.append(
                        {
                            "evidence_id": evidence_id,
                            "path": evidence.path,
                            "start_line": evidence.start_line,
                            "end_line": evidence.end_line,
                        }
                    )
        # Ranking retains evidence/chunk identity internally; only the
        # public response hides identifiers the caller did not request.
        for row in indexed_match_rows:
            row.pop("chunk_id", None)
            if not return_evidence_ids:
                row.pop("evidence_id", None)
    elif use_index == "always" and not eligible:
        # ``use_index='always'`` is fail-closed: when the index
        # cannot serve the query, we return a structured error
        # carrying the canonical reason code. The caller asked for
        # the index; we never silently substitute live results.
        # Acceptance criterion 2: ``use_index='always'`` failures
        # return a structured reason-coded error, never an empty
        # success.
        if fallback_reason is None:
            fallback_reason = "pattern_not_fts_eligible" if not eligible else "no_index_handle"
        _queue_recovery(workspace_root, fallback_reason)
        raise InvalidParamsError(
            f"use_index='always' cannot serve this query: reason_code={fallback_reason}"
        )
    elif use_index == "always" and store is None:
        raise InvalidParamsError(
            "use_index='always' requires an indexed workspace; the "
            "explore index is not attached to this session."
        )
    else:
        # use_index == 'never' OR store missing OR non-eligible pattern.
        if use_index == "auto" and fallback_reason is None:
            fallback_reason = "pattern_not_fts_eligible" if not eligible else "no_index_handle"
        _queue_recovery(workspace_root, fallback_reason)
        # Fall back to live grep.
        live_matches, skipped, truncated = _live_grep(
            workspace,
            pattern=pattern,
            path=path,
            normalized=normalized,
            is_regex=is_regex,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            include=include,
            exclude=exclude,
            context_before=context_before,
            context_after=context_after,
            limit=limit,
            max_file_bytes=max_file_bytes,
        )
        result = {
            "pattern": pattern,
            "base": path,
            "matches": live_matches,
            "truncated": truncated,
            "skipped_files": skipped,
            "ranked_by": rank_by,
            "dedupe_by_symbol": dedupe_by_symbol,
            # Live fallback: the explore index is not attached, so
            # graph context is not available. The structured reason
            # mirrors the indexed path's missing-data value so
            # callers can audit the absence.
            "graph_context": (
                [] if include_graph_context else f"graph_context:{INDEXED_COMPONENT_NOT_AVAILABLE}"
            ),
        }
        if return_evidence_ids:
            # When the caller asks for evidence ids in live mode we
            # synthesize an empty list to preserve the contract shape.
            result["evidence_ids"] = []
        result.update(
            _freshness_for_grep(session, index_used=False, fallback_reason=fallback_reason)
        )
        return ToolResult(
            content=[ToolContent.text_content(_tool_json(result))],
            is_error=False,
        )

    freshness = _freshness_for_grep(session, index_used=index_used, fallback_reason=fallback_reason)
    result = {
        "pattern": pattern,
        "base": path,
        "matches": indexed_match_rows,
        "truncated": len(indexed_match_rows) >= limit,
        "skipped_files": 0,
        "ranked_by": rank_by,
        "dedupe_by_symbol": dedupe_by_symbol,
        "graph_context": (
            graph_context
            if include_graph_context
            else f"graph_context:{INDEXED_COMPONENT_NOT_AVAILABLE}"
        ),
        "score_reasons": ([item.reasons for item in ranked_items] if ranked_items else []),
    }
    if return_evidence_ids:
        result["evidence_ids"] = [row.get("evidence_id") for row in indexed_match_rows]
    result.update(freshness)
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(result))],
        is_error=False,
    )
