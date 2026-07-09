"""Grep/content-search handler."""

from __future__ import annotations

import re
import sqlite3
import time
from typing import TYPE_CHECKING

from ralph.mcp.explore.dirty_paths import resolve_explore_index
from ralph.mcp.explore.ranking import (
    PHASE2_DISABLED_NOTE,
    fts_query_for,
    is_fts_eligible,
    score_grep_match,
    sort_ranked,
)
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
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
    from ralph.mcp.explore.store import ExploreStore
    from ralph.workspace import Workspace

from collections.abc import Iterable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.mcp.explore.store import EvidenceRow

# --- Index metadata helpers -----------------------------------------------


def _freshness_for_grep(
    session: object,
    *,
    index_used: bool,
    fallback_reason: str | None = None,
) -> dict[str, object]:
    """Return the freshness metadata block for a grep response.

    Returns an empty dict when the index is disabled so the legacy
    shape is preserved.
    """
    handle = resolve_explore_index(session)
    if handle is None:
        # No handle at all: legacy shape, but we still report
        # ``index_used=false`` so callers can detect the fall-back.
        return {
            "index_used": index_used,
            "index_generation": 0,
            "is_stale": False,
            "dirty_paths_count": 0,
            "stale_paths_count": 0,
            "fallback_reason": fallback_reason,
        }
    store: ExploreStore | None = getattr(handle, "store", None)
    if store is None:
        return {
            "index_used": index_used,
            "index_generation": 0,
            "is_stale": False,
            "dirty_paths_count": 0,
            "stale_paths_count": 0,
            "fallback_reason": fallback_reason,
        }
    generation_raw = store.get_setting("current_generation") or "0"
    try:
        generation_int = int(generation_raw)
    except (TypeError, ValueError):
        generation_int = 0
    dirty = store.peek_dirty_paths()
    return {
        "index_used": index_used,
        "index_generation": generation_int,
        "is_stale": bool(dirty),
        "dirty_paths_count": len(dirty),
        "stale_paths_count": 0,
        "fallback_reason": fallback_reason,
    }


def _indexed_matches(
    store: ExploreStore,
    pattern: str,
    *,
    whole_word: bool,
    limit: int,
) -> list[dict[str, object]]:
    """Run an FTS5 search and translate rows to the live match shape.

    Each returned ``evidence_id`` is a real row in the ``evidence``
    table so ``read_file(evidence_id=...)`` resolves to the exact
    span instead of returning ``unknown_evidence``. The translation
    looks up the chunk's stored line range and content hash, then
    inserts (or refreshes) the evidence row keyed by the prompt's
    deterministic evidence-id formula.
    """
    fts_query = fts_query_for(pattern, whole_word=whole_word)
    raw_rows = store.fts_search(fts_query, limit=max(limit, 1))
    rows: list[sqlite3.Row] = list(raw_rows)
    matches: list[dict[str, object]] = []
    for row in rows:
        raw_path_value: object = row["path"]
        raw_chunk_id_value: object = row["chunk_id"]
        try:
            snippet_value: object = row["snippet"]
        except IndexError:
            snippet_value = ""
        path_str = str(raw_path_value) if raw_path_value is not None else ""
        chunk_id_str = (
            str(raw_chunk_id_value) if raw_chunk_id_value is not None else ""
        )
        snippet_str = str(snippet_value) if snippet_value is not None else ""
        evidence_id = _ensure_grep_evidence_row(store, chunk_id_str)
        matches.append(
            {
                "path": path_str,
                "line": None,
                "text": snippet_str,
                "evidence_id": evidence_id,
            }
        )
    return matches


def _ensure_grep_evidence_row(store: ExploreStore, chunk_id: str) -> str:
    """Translate a chunk_id into a real ``evidence_id`` row.

    The chunk row carries path/line range/text_hash. We compute the
    prompt-exact evidence id from those deterministic inputs so the
    handle is stable across reindex, and we insert the row if it
    does not exist yet. Returns the evidence_id string (or the
    chunk_id when the chunk row is missing so the caller still has a
    stable handle).
    """
    if not chunk_id:
        return ""
    chunk_row: sqlite3.Row | None = None
    try:
        fetched: object = store._conn.execute(
            "SELECT path, start_line, end_line, text_hash, generation "
            "FROM chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if fetched is not None and type(fetched) is not type(None):
            chunk_row = cast("sqlite3.Row", fetched)
    except sqlite3.OperationalError:
        chunk_row = None
    if chunk_row is None:
        return chunk_id
    path_obj: object = chunk_row["path"]
    start_line_obj: object = chunk_row["start_line"]
    end_line_obj: object = chunk_row["end_line"]
    text_hash_obj: object = chunk_row["text_hash"]
    generation_obj: object = chunk_row["generation"]
    path = str(path_obj)
    start_line = int(start_line_obj) if isinstance(start_line_obj, int) else 0
    end_line = int(end_line_obj) if isinstance(end_line_obj, int) else 0
    text_hash = str(text_hash_obj)
    generation = int(generation_obj) if isinstance(generation_obj, int) else 0
    # The content_hash for an indexed chunk is the text_hash until
    # the row-level file content_hash replaces it. The explore
    # store's file row already carries the SHA-256 of the file.
    file_row = store.get_file(path)
    content_hash = file_row.content_hash if file_row is not None else text_hash
    evidence_id = _derive_evidence_id_for_span(
        path=path,
        content_hash=content_hash,
        start_line=start_line,
        end_line=end_line,
        kind="chunk",
    )
    # Insert or refresh. ``is_stale=False`` because the chunk row is
    # the source of truth right now; staleness is detected when
    # read_file(evidence_id=...) hashes the file and finds drift.
    import contextlib

    with contextlib.suppress(sqlite3.IntegrityError, sqlite3.OperationalError):
        store.insert_evidence(
            _EvidenceRowBuilder(
                evidence_id=evidence_id,
                path=path,
                start_line=start_line,
                end_line=end_line,
                content_hash=content_hash,
                generation=generation,
                source_tool="grep_files",
                evidence_kind="chunk",
            ).build()
        )
    return evidence_id


def _derive_evidence_id_for_span(
    *,
    path: str,
    content_hash: str,
    start_line: int,
    end_line: int,
    kind: str,
) -> str:
    """Compute the prompt-exact evidence id from deterministic inputs.

    Centralized here so the grep handler and the reindex pipeline
    produce identical ids for the same file span.
    """
    from ralph.mcp.explore.store import derive_evidence_id

    return derive_evidence_id(
        path=path,
        content_hash=content_hash,
        start_line=start_line,
        end_line=end_line,
        kind=kind,
        extractor_version="phase2-structure-v1",
    )


class _EvidenceRowBuilder:
    """Tiny helper that builds an ``EvidenceRow`` from span inputs."""

    def __init__(
        self,
        *,
        evidence_id: str,
        path: str,
        start_line: int,
        end_line: int,
        content_hash: str,
        generation: int,
        source_tool: str,
        evidence_kind: str,
    ) -> None:
        self.evidence_id = evidence_id
        self.path = path
        self.start_line = start_line
        self.end_line = end_line
        self.content_hash = content_hash
        self.generation = generation
        self.source_tool = source_tool
        self.evidence_kind = evidence_kind

    def build(self) -> EvidenceRow:
        from ralph.mcp.explore.store import EvidenceRow

        return EvidenceRow(
            evidence_id=self.evidence_id,
            path=self.path,
            start_line=self.start_line,
            end_line=self.end_line,
            content_hash=self.content_hash,
            generation=self.generation,
            source_tool=self.source_tool,
            evidence_kind=self.evidence_kind,
            created_at=time.time(),
            is_stale=False,
        )


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
    include_list: list[object] = (
        list(cast("Iterable[object]", include)) if include else []
    )
    exclude_list: list[object] = (
        list(cast("Iterable[object]", exclude)) if exclude else []
    )
    for file_path in all_files:
        if include_list and not any(
            match_glob(file_path, str(p)) for p in include_list
        ):
            continue
        if exclude_list and any(
            match_glob(file_path, str(p)) for p in exclude_list
        ):
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
        store_value: ExploreStore | None = getattr(handle, "store", None)
    else:
        store_value = None
    store: ExploreStore | None = store_value

    # Determine if FTS is eligible.
    eligible = is_fts_eligible(pattern, is_regex=is_regex, whole_word=whole_word)
    index_used = False
    fallback_reason: str | None = None
    from ralph.mcp.explore.ranking import RankedItem

    ranked_items: list[RankedItem] = []
    indexed_match_rows: list[dict[str, object]] = []

    if use_index != "never" and store is not None and eligible:
        indexed_match_rows = _indexed_matches(
            store, pattern, whole_word=whole_word, limit=limit
        )
        index_used = True
        if not return_evidence_ids:
            # Strip the explicit evidence_id from each match when the
            # caller did not request it. The handle is still on disk.
            for row in indexed_match_rows:
                row.pop("evidence_id", None)
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
                # Phase 1 has no per-file git-changed signal; the
                # caller can pass it via params['git_changed_paths']
                # if needed. We default to False here.
                ranked_items.append(
                    score_grep_match(
                        path=path_v,
                        line=line_v,
                        evidence_id=ev,
                    )
                )
            ranked_items = sort_ranked(ranked_items)
            # Apply the same order to the match rows.
            order = {item.key: idx for idx, item in enumerate(ranked_items)}
            indexed_match_rows.sort(
                key=lambda r: order.get(
                    f"{r.get('path', '')}:{r.get('line', '')}:"
                    f"{r.get('evidence_id', '')}",
                    len(order),
                )
            )
    elif use_index == "always" and not eligible:
        raise InvalidParamsError(
            "use_index='always' requires an FTS-eligible pattern; "
            "the requested pattern contains regex metacharacters or "
            "is not representable in FTS5 without changing semantics."
        )
    elif use_index == "always" and store is None:
        raise InvalidParamsError(
            "use_index='always' requires an indexed workspace; the "
            "explore index is not attached to this session."
        )
    else:
        # use_index == 'never' OR store missing OR non-eligible pattern.
        if use_index == "auto":
            fallback_reason = (
                "pattern_not_fts_eligible"
                if not eligible
                else "no_index_handle"
            )
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
            "graph_context": (
                [] if include_graph_context else "disabled:phase2"
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

    freshness = _freshness_for_grep(
        session, index_used=index_used, fallback_reason=fallback_reason
    )
    result = {
        "pattern": pattern,
        "base": path,
        "matches": indexed_match_rows,
        "truncated": len(indexed_match_rows) >= limit,
        "skipped_files": 0,
        "ranked_by": rank_by,
        "dedupe_by_symbol": dedupe_by_symbol,
        "graph_context": (
            [] if include_graph_context else f"disabled:{PHASE2_DISABLED_NOTE}"
        ),
        "score_reasons": (
            [item.reasons for item in ranked_items]
            if ranked_items
            else []
        ),
    }
    if return_evidence_ids:
        result["evidence_ids"] = [
            row.get("evidence_id") for row in indexed_match_rows
        ]
    result.update(freshness)
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(result))],
        is_error=False,
    )
