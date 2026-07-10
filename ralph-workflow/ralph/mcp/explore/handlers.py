"""MCP handlers for the explore index: ``ralph_index_status`` and ``ralph_reindex``.

Both handlers operate on an optional, lazily-initialized
:class:`ExploreIndex` handle attached to the session. When the index
is disabled or missing, both handlers return a structured "disabled"
response rather than raising — this keeps the live tool surface
unchanged for existing callers.

The handlers own the ExploreIndex handle and the path-resolution
logic so they are testable in isolation. Tests construct a
``_SqliteBackedExploreIndex`` directly over a ``tmp_path`` workspace
and pass it as ``session.explore_index``.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.explore import graph as graph_module
from ralph.mcp.explore.pipeline import (
    DEFAULT_FULL_TIMEOUT_MS as _REINDEX_TIMEOUT_MAX_MS,
)
from ralph.mcp.explore.pipeline import (
    DEFAULT_TIMEOUT_MS,
    ReindexOptions,
    ReindexResult,
    reindex,
)
from ralph.mcp.explore.store import (
    DEFAULT_INDEX_DB,
    DEFAULT_INDEX_ROOT,
    ExploreStore,
    normalize_index_path,
    row_str,
)
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.workspace._utils import (
    WORKSPACE_METADATA_READ_CAPABILITY,
    WORKSPACE_READ_CAPABILITY,
    _tool_json,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.workspace.protocol import Workspace


logger = logging.getLogger(__name__)


# --- Explore index handle -------------------------------------------------


@dataclass
class ExploreIndex:
    """Optional, lazily-initialized handle on the session/workspace.

    Stores the index directory + a live :class:`ExploreStore`. The
    handle is created by the MCP server bootstrap (or by tests) and
    injected as ``session.explore_index``. When ``None``, handlers
    behave exactly as today — no metadata added, no dirty marking.

    The handle exposes a single public surface to MCP handlers:
    ``mark_dirty(paths, source_tool, reason)`` so the
    dirty-path tracking stays narrow and testable.
    """

    workspace_root: Path
    index_root: Path
    store: ExploreStore
    last_refresh_kind: str = "none"
    cold_index_required: bool = False
    generation: int = 0
    last_job_status: str | None = None
    #: Set by a reindex writer while it owns the work; the mutation
    #: freshness block reports it. Production writers flip this to
    #: ``True`` for the duration of the job and back to ``False`` on
    #: success/failure. The default ``False`` keeps the attribute
    #: optional for test doubles that subclass or duck-type the handle.
    reindex_in_progress: bool = False

    @property
    def index_dir(self) -> Path:
        return self.index_root

    def mark_dirty(
        self, paths: Sequence[str], *, source_tool: str, reason: str = "mutated"
    ) -> None:
        for path in paths:
            self.store.mark_dirty(path, reason=reason, source_tool=source_tool)

    def index_storage_bytes(self) -> int:
        return self.store.index_storage_bytes()

    def build_options(self, *, timeout_ms: int) -> ReindexOptions:
        return ReindexOptions(mode="changed", timeout_ms=timeout_ms)


def _resolve_index_dir(workspace_root: Path) -> Path:
    """Return the canonical index directory under ``.agent/ralph-explore``."""
    return Path(workspace_root) / DEFAULT_INDEX_ROOT


def build_explore_index(workspace_root: Path) -> ExploreIndex:
    """Construct a fresh ExploreIndex for ``workspace_root``.

    Tests call this directly. Production callers should defer
    construction to the MCP server bootstrap so the index is created
    lazily and only when first queried.

    AC-05/AC-06: when the persisted ``schema_version`` or
    ``extractor_version`` settings disagree with the runtime
    constants, the on-disk index is wiped and a safe cold rebuild is
    triggered on the next call. The handle is still returned so the
    caller can observe the wiped state via ``index_exists=True`` and
    ``cold_index_required=True``.
    """
    workspace_root = Path(workspace_root).resolve()
    index_root = _resolve_index_dir(workspace_root)
    store = ExploreStore(index_root)
    from ralph.mcp.explore.pipeline import EXTRACTOR_VERSION
    from ralph.mcp.explore.store import SCHEMA_VERSION
    from ralph.mcp.explore.structure import EXTRACTOR_VERSION as STRUCTURE_EXTRACTOR_VERSION
    raw = store.get_setting("current_generation") or "0"
    try:
        generation = int(raw)
    except ValueError:
        generation = 0
    persisted_schema = store.get_setting("schema_version")
    persisted_extractor = store.get_setting("extractor_version")
    persisted_structure = store.get_setting("structure_extractor_version")
    if persisted_schema is None and store.index_storage_bytes() > 0:
        # Legacy / hand-written index without a schema version: wipe
        # it to avoid undefined reads from incompatible rows.
        try:
            index_db = index_root / DEFAULT_INDEX_DB
            for suffix in ("", "-wal", "-shm"):
                target = Path(str(index_db) + suffix)
                if target.exists():
                    target.unlink()
        except OSError:
            pass
        store = ExploreStore(index_root)
        generation = 0
    elif (
        persisted_schema is not None
        and persisted_schema != SCHEMA_VERSION
    ):
        # Incompatible persisted index: safe cold rebuild.
        try:
            index_db = index_root / DEFAULT_INDEX_DB
            for suffix in ("", "-wal", "-shm"):
                target = Path(str(index_db) + suffix)
                if target.exists():
                    target.unlink()
        except OSError:
            pass
        store = ExploreStore(index_root)
        generation = 0
    elif (
        persisted_extractor is not None
        and persisted_extractor != EXTRACTOR_VERSION
    ):
        try:
            index_db = index_root / DEFAULT_INDEX_DB
            for suffix in ("", "-wal", "-shm"):
                target = Path(str(index_db) + suffix)
                if target.exists():
                    target.unlink()
        except OSError:
            pass
        store = ExploreStore(index_root)
        generation = 0
    elif (
        persisted_structure is not None
        and persisted_structure != STRUCTURE_EXTRACTOR_VERSION
    ):
        # Structure rows are out of date but the lexical rows are
        # still safe: drop structure rows and the structure-extractor
        # key. The next reindex rebuilds the structure rows.
        try:
            store._conn.execute("DELETE FROM spans")
            store._conn.execute("DELETE FROM symbols")
            store._conn.execute("DELETE FROM edges")
            store._conn.commit()
        except Exception:
            pass
    latest_row: sqlite3.Row | None = store.latest_job()
    raw_status: object = (
        row_str(latest_row, "status") if latest_row is not None else ""
    )
    last_status: str | None
    if raw_status == "":
        last_status = None
    elif isinstance(raw_status, str):
        last_status = raw_status
    else:
        last_status = str(raw_status)
    return ExploreIndex(
        workspace_root=workspace_root,
        index_root=index_root,
        store=store,
        generation=generation,
        last_job_status=last_status,
    )


# --- Handler helpers ------------------------------------------------------


def _resolve_explore_index(session: object) -> ExploreIndex | None:
    """Return the explore index handle attached to ``session`` if any."""
    handle: ExploreIndex | None = getattr(session, "explore_index", None)
    if handle is None:
        return None
    return handle


def _gitignore_coverage(workspace_root: Path) -> bool:
    """Return True when the managed gitignore rule is present.

    AC-05: the explicit disposable-cache rule is ``.agent/ralph-explore/``
    (added to ``_DEFAULT_GITIGNORE_PATTERNS``). The parent
    ``.agent/`` rule is also accepted because it covers the same
    path via trailing-slash semantics. The handler reports the
    truthful union of coverage rather than requiring an exact
    rule match so existing ``.agent/``-only gitignores continue
    to be honored. The repair metadata distinguishes between the
    two cases (see ``_gitignore_repair_payload``).
    """
    gitignore = Path(workspace_root) / ".gitignore"
    if not gitignore.is_file():
        return False
    try:
        text = gitignore.read_text(encoding="utf-8")
    except OSError:
        return False
    stripped_lines = {line.strip() for line in text.splitlines()}
    if ".agent/ralph-explore/" in stripped_lines:
        return True
    return ".agent/" in stripped_lines or ".agent" in stripped_lines or "/.agent/" in stripped_lines


# --- MCP handlers ---------------------------------------------------------


def handle_ralph_index_status(
    session: CoordinationSessionLike,
    workspace: object,
    params: dict[str, object],
) -> ToolResult:
    """Report index health and freshness.

    Capability: ``WorkspaceMetadataRead`` (read-only metadata).

    Side-effect free contract: this handler MUST NOT create SQLite
    files, ``.agent/ralph-explore/`` directories, or modify gitignore
    state. When the session has no explore index handle, it inspects
    the existing on-disk state and reports ``enabled=False`` /
    ``index_exists=False`` so callers can decide whether to run
    ``ralph_reindex``.
    """
    require_capability(
        session, WORKSPACE_METADATA_READ_CAPABILITY, "Explore index status"
    )
    workspace_root_obj: object = getattr(workspace, "root", None)
    workspace_root_raw: object = workspace_root_obj or params.get(
        "workspace_root", ""
    )
    workspace_root_str: str = (
        str(workspace_root_raw) if workspace_root_raw else ""
    )
    workspace_root = Path(workspace_root_str) if workspace_root_str else Path.cwd()
    handle: ExploreIndex | None = _resolve_explore_index(session)
    if handle is None:
        # Side-effect free: inspect existing disk state without
        # creating files. ``enabled=False`` reports the absence; the
        # caller decides whether to run ``ralph_reindex`` to enable it.
        index_dir = _resolve_index_dir(workspace_root)
        db_path = index_dir / "index.sqlite"
        index_exists_on_disk = db_path.is_file()
        cold_index_required = not index_exists_on_disk
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        _build_disabled_status_payload(
                            workspace_root,
                            cold_index_required=cold_index_required,
                            index_exists=index_exists_on_disk,
                        )
                    )
                )
            ],
            is_error=False,
        )
    cold_index_required = handle.generation == 0
    payload = _build_status_payload(handle, workspace_root, cold_index_required)
    payload["enabled"] = True
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=False,
    )


def _build_disabled_status_payload(
    workspace_root: Path,
    *,
    cold_index_required: bool,
    index_exists: bool = False,
) -> dict[str, object]:
    """Return the side-effect-free status payload when no handle exists.

    ``index_exists`` lets the caller surface an existing on-disk
    persisted index even when the current session has no handle
    attached, so a side-effect-free status check reports the real
    disk state instead of always reporting ``index_exists=False``.

    AC-04: when ``.agent/ralph-explore/`` is not covered by the
    managed gitignore rule, ``managed_ignore_rule_repair`` carries
    the next Ralph seeding repair instruction so callers know how
    to make the cache disposable. The status handler never mutates
    the gitignore on its own \u2014 the repair is a documented next
    step, not a side effect.
    """
    return {
        "enabled": False,
        "index_exists": index_exists,
        "generation": 0,
        "indexed_at": None,
        "files_indexed": 0,
        "files_stale": 0,
        "last_job": None,
        "capabilities": [],
        "graph_backend": "sqlite",
        "dirty_paths_count": 0,
        "cold_index_required": cold_index_required,
        "last_refresh_kind": "none",
        "is_stale": False,
        "stale_paths_count": 0,
        "index_storage_bytes": 0,
        "managed_ignore_rule_present": _gitignore_coverage(workspace_root),
        "managed_ignore_rule_repair": _gitignore_repair_payload(workspace_root),
    }


def _gitignore_repair_payload(workspace_root: Path) -> dict[str, object]:
    """Return the structured next-Ralph-seeding repair for the explore ignore rule.

    The repair is a documented next step, not a side effect. The
    handler must NOT mutate the gitignore; it only reports the
    instruction so an operator (or the next ``ralph`` invocation,
    which already calls ``auto_seed_default_gitignore``) can fix the
    coverage.

    AC-05: the repair payload names the explicit ``.agent/ralph-explore/``
    child rule so the next ``auto_seed_default_gitignore`` pass
    appends it next to the parent ``.agent/`` rule. Parent-only
    coverage is reported as ``"parent_only"`` so operators and tests
    see whether the explicit child rule is present yet; ``action``
    becomes ``"append_explicit_child_rule"`` so the next normal
    Ralph seeding pass repairs the gap instead of leaving the
    parent-only state in place.
    """
    gitignore = Path(workspace_root) / ".gitignore"
    rule_present = _gitignore_coverage(workspace_root)
    if rule_present:
        # Parent ``.agent/`` coverage plus the explicit child rule
        # is the fully repaired state. Both reports surface the
        # current truth so callers do not need to learn a new
        # status string for the existing-parent-only case.
        if _gitignore_child_rule_present(workspace_root):
            return {
                "required": False,
                "action": "none",
                "reason": "managed_ignore_rule_present",
                "coverage": "explicit_child_rule",
            }
        return {
            "required": True,
            "action": "append_explicit_child_rule",
            "reason": "managed_ignore_rule_parent_only",
            "coverage": "parent_only",
            "missing_rule": ".agent/ralph-explore/",
            "target_file": str(gitignore),
            "next_command": "ralph",
            "description": (
                "Parent ``.agent/`` coverage is in place but the "
                "explicit ``.agent/ralph-explore/`` child rule is "
                "missing. Run a normal `ralph` invocation (or "
                "`auto_seed_default_gitignore`) to append the child "
                "rule so the disposable explore cache is not "
                "committed."
            ),
        }
    return {
        "required": True,
        "action": "seed_default_gitignore",
        "reason": "managed_ignore_rule_missing",
        "target_file": str(gitignore),
        "patterns_to_append": [".agent/", ".agent/ralph-explore/"],
        "next_command": "ralph",
        "description": (
            "Run a normal `ralph` invocation (or "
            "`auto_seed_default_gitignore`) to seed the default "
            ".gitignore so .agent/ralph-explore/ stays a "
            "disposable cache and is not committed. Both the "
            "parent ``.agent/`` rule and the explicit "
            "``.agent/ralph-explore/`` child rule will be appended."
        ),
    }


def _gitignore_child_rule_present(workspace_root: Path) -> bool:
    """Return True when the explicit ``.agent/ralph-explore/`` rule is present."""
    gitignore = Path(workspace_root) / ".gitignore"
    if not gitignore.is_file():
        return False
    try:
        text = gitignore.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(
        line.strip() == ".agent/ralph-explore/" for line in text.splitlines()
    )


def _build_status_payload(
    handle: ExploreIndex,
    workspace_root: Path,
    cold_index_required: bool,
) -> dict[str, object]:
    store = handle.store
    latest_row: sqlite3.Row | None = store.latest_job()
    finished_value: str = (
        row_str(latest_row, "finished_at") if latest_row is not None else ""
    )
    indexed_at: float | None
    if finished_value == "":
        indexed_at = None
    else:
        try:
            indexed_at = float(finished_value)
        except ValueError:
            indexed_at = None
    dirty_paths = store.peek_dirty_paths()
    # Ponytail: bounded aggregates, not ``iter_files()``; both
    # are single ``COUNT(*)`` queries so the status call does
    # not materialize the entire ``files`` table.
    files_indexed = store.count_files()
    # Ponytail: ``files_stale`` counts deleted files; ``is_stale``
    # is true when dirty paths exist OR a deleted file still has
    # a row. ``iter_files`` filters out deleted rows, so the
    # count must come from a dedicated aggregate.
    stale_paths = store.count_deleted_files()
    is_stale = bool(dirty_paths) or stale_paths > 0
    last_job_dict: dict[str, object] | None
    if latest_row is None:
        last_job_dict = None
    else:
        # Ponytail: ``sqlite3.Row`` iterates values, not column names.
        # ``dict(row)`` uses ``row.keys()`` internally and stringifies
        # values, so we do not need a manual comprehension here.
        row_dict: dict[str, object] = dict(latest_row)
        last_job_dict = {str(key): str(value) for key, value in row_dict.items()}
    return {
        "index_exists": handle.generation > 0,
        "generation": handle.generation,
        "indexed_at": indexed_at,
        "files_indexed": files_indexed,
        "files_stale": stale_paths,
        "last_job": last_job_dict,
        "capabilities": ["evidence_lookup", "fts_search"],
        "graph_backend": "sqlite",
        "dirty_paths_count": len(dirty_paths),
        "cold_index_required": cold_index_required,
        "last_refresh_kind": handle.last_refresh_kind,
        "is_stale": is_stale,
        "stale_paths_count": stale_paths,
        "index_storage_bytes": handle.index_storage_bytes(),
        "managed_ignore_rule_present": _gitignore_coverage(workspace_root),
        "managed_ignore_rule_repair": _gitignore_repair_payload(workspace_root),
    }


def handle_ralph_reindex(
    session: CoordinationSessionLike,
    workspace: object,
    params: dict[str, object],
) -> ToolResult:
    """Run a bounded changed/full reindex.

    Capability: ``WorkspaceRead`` (the reindex touches workspace
    files). Production callers are expected to gate reindex behind a
    higher privilege in the future; the Phase 1 contract keeps the
    current capability.

    AC-05: ``timeout_ms`` is bounded. The handler rejects values
    outside ``[1, _REINDEX_TIMEOUT_MAX_MS]`` rather than forwarding
    arbitrarily large values into ``ReindexOptions``. Malformed
    (non-integer, non-string-int) values are also rejected; callers
    must send a positive integer in the documented range.
    """
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Explore reindex")
    mode_raw: object = params.get("mode", "changed")
    mode: str = str(mode_raw) if not isinstance(mode_raw, str) else mode_raw
    if mode not in {"changed", "full"}:
        raise InvalidParamsError(
            f"Invalid reindex mode: {mode!r}; expected 'changed' or 'full'"
        )
    # AC-05: bounded per-call budget. Malformed or out-of-range
    # values fail closed with a structured tool error; the
    # dispatcher must NOT silently fall back to the default when
    # the caller sent garbage.
    timeout_ms = _strict_int_param(
        params,
        "timeout_ms",
        default=DEFAULT_TIMEOUT_MS,
        min_value=1,
        max_value=_REINDEX_TIMEOUT_MAX_MS,
    )
    path_scope_raw: object = params.get("path_scope")
    path_scope: tuple[str, ...] = ()
    if isinstance(path_scope_raw, list):
        normalized_scope: list[str] = []
        for p in path_scope_raw:
            if not isinstance(p, (str, int, float)):
                continue
            try:
                normalized_scope.append(normalize_index_path(str(p)))
            except ValueError as exc:
                # AC-05: surface invalid path_scope as a structured
                # tool error before reindexing rather than letting
                # the rejection propagate as a generic exception.
                raise InvalidParamsError(
                    f"Invalid path_scope entry {p!r}: {exc}"
                ) from exc
        path_scope = tuple(normalized_scope)

    workspace_root_obj2: object = getattr(workspace, "root", None)
    workspace_root_raw2: object = workspace_root_obj2 or params.get(
        "workspace_root", ""
    )
    workspace_root_str2: str = (
        str(workspace_root_raw2) if workspace_root_raw2 else ""
    )
    workspace_root = Path(workspace_root_str2) if workspace_root_str2 else Path.cwd()

    handle: ExploreIndex | None = _resolve_explore_index(session)
    cold_built = False
    if handle is None:
        handle = build_explore_index(workspace_root)
        # The first call from a session without an explore index is
        # typically the cold build; tag it so downstream consumers
        # can decide whether to block on it.
        handle.cold_index_required = handle.generation == 0
        cold_built = True
        # AC-03: persist the cold-built handle on the session so
        # subsequent indexed read/search/grep/list/edit operations
        # observe the same handle (and therefore the same generation
        # + dirty-path state). Also surface it on the workspace so
        # helpers that take the workspace object (e.g. the file
        # mutation handlers) can find it without re-walking the
        # session.
        import contextlib

        with contextlib.suppress(Exception):
            session.explore_index = handle
        with contextlib.suppress(Exception):
            # The ``workspace`` parameter is typed as ``object`` to
            # avoid pulling the full Workspace protocol into this
            # handler. Production workspaces expose ``explore_index``
            # via the Workspace protocol; the attribute assignment is
            # wrapped in suppress so legacy workspaces stay valid.
            # Cast to the Workspace protocol so direct attribute
            # assignment is the canonical, non-setattr path; the
            # assignment still goes through the protocol's optional
            # surface so legacy workspaces can ignore the attribute
            # without errors.
            cast("Workspace", workspace).explore_index = handle
    _ = cold_built  # reserved for future payload/audit fields


    started_at = time.time()
    options = ReindexOptions(
        mode=mode,
        timeout_ms=timeout_ms,
        path_scope=path_scope,
    )
    # AC-05: bounded cancel contract for ralph_reindex. The schema
    # exposes ``cancel: bool``; when set, the handler installs a
    # per-request cancel flag (keyed by a fresh token) that the
    # reindex writer polls at phase boundaries. Concurrent
    # reindex calls against the same session get distinct tokens,
    # so one caller's cancel never cancels or clears another
    # caller's flag. On cancel the prior committed generation is
    # preserved (no mutable work is exposed) and the response
    # carries ``cancelled=true`` with a bounded incomplete
    # summary.
    cancel_raw: object = params.get("cancel", False)
    cancel_flag = bool(cancel_raw) if isinstance(cancel_raw, bool) else False
    reindex_cancel_token = _new_cancel_token()
    cancel_callable: Callable[[], bool] = _arm_cancel_flag(
        _REINDEX_CANCEL_FLAGS,
        _REINDEX_CANCEL_LOCK,
        reindex_cancel_token,
        cancel_flag,
    )
    try:
        result = reindex(
            handle.store,
            handle.workspace_root,
            options=options,
            cancel=cancel_callable,
        )
    except Exception as exc:
        logger.exception("ralph_reindex crashed: %s", exc)
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                            "elapsed_seconds": time.time() - started_at,
                        }
                    )
                )
            ],
            is_error=True,
        )
    finally:
        # AC-02/AC-05: clear the per-request cancel flag at every
        # exit path so a previous caller's cancel cannot poison a
        # subsequent reindex against the same session. The token
        # is unique to this call, so the pop never deletes a
        # concurrent caller's flag.
        _disarm_cancel_flag(
            _REINDEX_CANCEL_FLAGS,
            _REINDEX_CANCEL_LOCK,
            reindex_cancel_token,
        )
    handle.generation = result.generation
    handle.last_job_status = result.status
    handle.last_refresh_kind = "full" if mode == "full" else "changed"
    payload = _build_reindex_payload(result)
    payload["cancelled"] = result.status == "cancelled"
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=False,
    )


def _build_reindex_payload(result: ReindexResult) -> dict[str, object]:
    return {
        "job_id": result.job_id,
        "job_status": result.status,
        "generation": result.generation,
        "changed_files": list(result.changed_files),
        "failed_files": list(result.failed_files),
        "parse_count": result.parse_count,
        "dirty_paths_count": result.dirty_paths_count,
        "elapsed_seconds": result.elapsed_seconds,
        "error_summary": result.error_summary,
    }


# --- ralph_graph handler (AC-07) ------------------------------------------


_VALID_GRAPH_QUERY_TYPES: tuple[str, ...] = (
    "neighbors",
    "path",
    "impact",
    "hubs",
    "tests",
)
_VALID_FRESHNESS: tuple[str, ...] = ("required", "prefer_fresh", "allow_stale")
_VALID_CHANGE_KINDS: tuple[str, ...] = (
    "rename",
    "signature",
    "behavior",
    "delete",
    "unknown",
)
#: Prompt-exact upper bound for the ``limit`` parameter (the graph
#: contract always exposes the same cap).
_GRAPH_LIMIT_MAX: int = 100
#: Default per-call budget for ``ralph_graph`` queries, in
#: milliseconds. Picked to match the existing reindex default
#: (5 s) so a single tool call does not silently outlive its
#: expected budget.
_GRAPH_DEFAULT_TIMEOUT_MS: int = 5_000
#: Maximum permissible ``timeout_ms`` for ``ralph_graph``. The
#: handler rejects any value outside ``[1, _GRAPH_TIMEOUT_MAX_MS]``
#: so callers cannot extend the budget arbitrarily. Matches the
#: default in the schema (1-30000).
_GRAPH_TIMEOUT_MAX_MS: int = 30_000
#: Cooperative-cancellation flag registry. The handler stores
#: ``True`` when the caller asked to cancel, keyed by a
#: per-request token (UUID). The dispatcher polls the flag at
#: phase boundaries. The token is generated at the start of
#: every call and removed in the ``finally`` block, so a
#: previous caller's cancel cannot poison a concurrent query
#: against the same session and the map cannot leak across
#: long-lived sessions.
#:
#: Ponytail: each entry is keyed by a unique token that lives
#: only for the duration of one call. Concurrent calls against
#: the same session get distinct tokens and never observe each
#: other's flags. The internal lock guards concurrent mutation
#: of the dict so a writer that arms its flag and a cleanup
#: path that pops a different call's flag cannot race.
_GRAPH_CANCEL_FLAGS: dict[str, bool] = {}  # bounded-accumulator-ok: keyed by request token; one entry per active call
_GRAPH_CANCEL_LOCK: threading.Lock = threading.Lock()

#: Per-request cancel flag for ralph_reindex. Mirrors the
#: ``_GRAPH_CANCEL_FLAGS`` contract — one entry per active call,
#: keyed by a unique request token, cleared on every exit path.
#: The reindex writer polls this flag at phase boundaries; when
#: set, the writer preserves the prior committed generation and
#: returns a ``cancelled`` result. Concurrent reindex calls
#: against the same session get distinct tokens.
_REINDEX_CANCEL_FLAGS: dict[str, bool] = {}  # bounded-accumulator-ok: keyed by request token; one entry per active call
_REINDEX_CANCEL_LOCK: threading.Lock = threading.Lock()


def _new_cancel_token() -> str:
    """Return a fresh per-request cancellation token (UUID4 string)."""
    return uuid.uuid4().hex


def _arm_cancel_flag(
    registry: dict[str, bool],
    lock: threading.Lock,
    token: str,
    initial: bool,
) -> Callable[[], bool]:
    """Register a per-request cancel flag and return its poll callable.

    The token is unique to this call. A concurrent call against
    the same session generates a different token, so concurrent
    callers cannot observe or mutate each other's flags. The
    returned callable reads the flag under ``lock`` so a writer
    that flips the flag does not race with the dispatcher's
    poll. Removal happens in the ``finally`` block via
    ``_disarm_cancel_flag``.
    """
    with lock:
        registry[token] = bool(initial)

    def _is_set() -> bool:
        with lock:
            return bool(registry.get(token, False))

    return _is_set


def _disarm_cancel_flag(
    registry: dict[str, bool],
    lock: threading.Lock,
    token: str,
) -> None:
    """Remove the per-request cancel flag entry on every exit path.

    The token-based key guarantees the pop never deletes a
    concurrent caller's flag. The lock prevents the pop from
    racing with a poll that has already loaded the entry.
    """
    with lock:
        registry.pop(token, None)


def _graph_node_to_dict(node: graph_module.GraphNode) -> dict[str, object]:
    return {
        "id": node.id,
        "kind": node.kind,
        "label": node.label,
        "path": node.path,
        "confidence": node.confidence,
        "provenance": node.provenance,
        "evidence_ids": list(node.evidence_ids),
    }


def _graph_edge_to_dict(edge: graph_module.GraphEdge) -> dict[str, object]:
    return {
        "source": edge.source,
        "target": edge.target,
        "relation": edge.relation,
        "path": edge.path,
        "confidence": edge.confidence,
        "provenance": edge.provenance,
        "reason": edge.reason,
        "evidence_id": edge.evidence_id,
    }


def _graph_result_to_dict(result: graph_module.GraphResult) -> dict[str, object]:
    return {
        "query_type": result.query_type,
        "nodes": [_graph_node_to_dict(n) for n in result.nodes],
        "edges": [_graph_edge_to_dict(e) for e in result.edges],
        "paths": [list(p) for p in result.paths],
        "impacted_files": list(result.impacted_files),
        "suggested_tests": [_graph_node_to_dict(n) for n in result.suggested_tests],
        "confidence": result.confidence,
        "provenance": result.provenance,
        "evidence_ids": list(result.evidence_ids),
        "missing_data": list(result.missing_data),
        "index_generation": result.index_generation,
        "is_stale": result.is_stale,
        "truncated": result.truncated,
        "cancelled": result.cancelled,
        "deadline_exceeded": result.deadline_exceeded,
        "metadata": dict(result.metadata),
    }


def handle_ralph_graph(
    session: CoordinationSessionLike,
    workspace: object,
    params: dict[str, object],
) -> ToolResult:
    """Bounded graph-native query over the indexed exploration substrate.

    Capability: ``WorkspaceRead``. Every response includes the
    prompt-exact shared fields (``nodes``, ``edges``, ``paths``,
    ``impacted_files``, ``suggested_tests``, ``confidence``,
    ``provenance``, ``evidence_ids``, ``missing_data``,
    ``index_generation``, ``is_stale``, ``truncated``,
    ``cancelled``, ``deadline_exceeded``).

    AC-05: ``timeout_ms`` (1-30000) is a bounded per-call budget.
    On deadline expiry the dispatcher returns a bounded, truthful
    incomplete result (``deadline_exceeded=true``,
    ``missing_data=("deadline_exceeded",)``) without exposing
    mutable work. ``cancel=true`` requests cooperative cancellation
    with the same bounded contract (``cancelled=true``,
    ``missing_data=("cancelled",)``).
    """
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Graph query")
    query_type_raw: object = params.get("query_type", "")
    if not isinstance(query_type_raw, str) or query_type_raw not in _VALID_GRAPH_QUERY_TYPES:
        raise InvalidParamsError(
            f"Invalid query_type: {query_type_raw!r}; expected one of "
            f"{', '.join(_VALID_GRAPH_QUERY_TYPES)}"
        )
    target = str(params.get("target", "")) if params.get("target") is not None else ""
    # AC-05: the documented contract is that ``target`` is required for
    # ``neighbors`` / ``path`` / ``impact`` / ``tests`` and optional only
    # for ``hubs``. A targetless request against the four required-target
    # query types would otherwise run a degenerate traversal that returns
    # no evidence; failing closed at the boundary prevents callers from
    # silently relying on an undocumented ``empty-target`` fallback.
    if not target and query_type_raw in {"neighbors", "path", "impact", "tests"}:
        raise InvalidParamsError(
            f"target is required for query_type={query_type_raw!r}; "
            "only 'hubs' accepts a targetless query."
        )
    target_b_raw = params.get("target_b")
    target_b: str | None = (
        str(target_b_raw) if isinstance(target_b_raw, str) and target_b_raw else None
    )
    relations_raw = params.get("relations")
    relations: tuple[str, ...] | None = None
    if isinstance(relations_raw, list):
        relations = tuple(str(rel) for rel in relations_raw if isinstance(rel, str))
    freshness_raw: object = params.get("freshness", "prefer_fresh")
    freshness: str = (
        str(freshness_raw) if isinstance(freshness_raw, str) else "prefer_fresh"
    )
    if freshness not in _VALID_FRESHNESS:
        raise InvalidParamsError(
            f"Invalid freshness: {freshness!r}; expected one of "
            f"{', '.join(_VALID_FRESHNESS)}"
        )
    limit = _int_param(params, "limit", 25)
    if limit < 1 or limit > _GRAPH_LIMIT_MAX:
        raise InvalidParamsError("limit must be between 1 and 100")
    direction = str(params.get("direction", "both"))
    if direction not in {"out", "in", "both"}:
        raise InvalidParamsError(
            f"Invalid direction: {direction!r}; expected 'out', 'in', or 'both'"
        )
    depth = _int_param(params, "depth", 1)
    max_paths = _int_param(params, "max_paths", 3)
    change_kind_raw: object = params.get("change_kind", "unknown")
    change_kind: str = (
        str(change_kind_raw) if isinstance(change_kind_raw, str) else "unknown"
    )
    if change_kind not in _VALID_CHANGE_KINDS:
        raise InvalidParamsError(
            f"Invalid change_kind: {change_kind!r}; expected one of "
            f"{', '.join(_VALID_CHANGE_KINDS)}"
        )
    scope_path_raw = params.get("scope_path")
    scope_path: str | None = (
        str(scope_path_raw)
        if isinstance(scope_path_raw, str) and scope_path_raw
        else None
    )
    role_raw = params.get("role")
    role: str | None = (
        str(role_raw) if isinstance(role_raw, str) and role_raw else None
    )
    # AC-05: bounded per-call deadline. Reject malformed values
    # fail-closed; only positive integers in [1, 30000] are
    # accepted. The deadline is converted to a monotonic-clock
    # absolute deadline so a future system-clock change cannot
    # extend the budget.
    timeout_ms = _strict_int_param(
        params,
        "timeout_ms",
        default=_GRAPH_DEFAULT_TIMEOUT_MS,
        min_value=1,
        max_value=_GRAPH_TIMEOUT_MAX_MS,
    )
    deadline = time.monotonic() + timeout_ms / 1000.0
    # AC-05: cooperative cancellation. ``cancel=true`` flips the
    # per-request flag to True; the dispatcher polls it at phase
    # boundaries. The flag is keyed by a fresh per-request token
    # so a previous caller's cancel cannot poison a new query and
    # concurrent queries against the same session get distinct
    # tokens that cannot observe each other.
    cancel_raw: object = params.get("cancel", False)
    cancel_flag = bool(cancel_raw) if isinstance(cancel_raw, bool) else False
    # AC-05: cooperative cancellation. The flag is registered
    # under a fresh per-request token so concurrent queries
    # against the same session get distinct entries; one caller's
    # cancel never cancels or clears another caller's flag. The
    # dispatcher polls the flag at phase boundaries. The entry is
    # explicitly removed in the ``finally`` block so repeated
    # long-lived sessions do not leak entries into the
    # module-global map.
    graph_cancel_token = _new_cancel_token()
    cancel_callable: Callable[[], bool] = _arm_cancel_flag(
        _GRAPH_CANCEL_FLAGS,
        _GRAPH_CANCEL_LOCK,
        graph_cancel_token,
        cancel_flag,
    )

    # AC-02/AC-05: track whether the graph call lazily built an
    # ephemeral index that no caller is responsible for closing.
    # The finally block closes the underlying SQLite store so the
    # per-call file handle is released before the next call.
    ephemeral_handle: ExploreIndex | None = None
    try:
        handle: ExploreIndex | None = _resolve_explore_index(session)
        if handle is None:
            workspace_root_obj: object = getattr(workspace, "root", None)
            workspace_root_raw: object = workspace_root_obj or params.get(
                "workspace_root", ""
            )
            workspace_root_str: str = (
                str(workspace_root_raw) if workspace_root_raw else ""
            )
            workspace_root = (
                Path(workspace_root_str)
                if workspace_root_str
                else Path.cwd()
            )
            handle = build_explore_index(workspace_root)
            ephemeral_handle = handle
        result = graph_module.run_query(
            handle.store,
            query_type=query_type_raw,
            target=target,
            target_b=target_b,
            relations=relations,
            limit=limit,
            freshness=freshness,
            direction=direction,
            depth=depth,
            max_paths=max_paths,
            change_kind=change_kind,
            scope_path=scope_path,
            role=role,
            deadline=deadline,
            cancel=cancel_callable,
        )
    finally:
        # AC-02/AC-05: bounded accumulator contract. The cancel
        # flag is scoped to a unique per-request token, not the
        # session lifetime, so the pop never deletes a concurrent
        # caller's flag. Concurrent queries against the same
        # session are isolated by their distinct tokens.
        _disarm_cancel_flag(
            _GRAPH_CANCEL_FLAGS,
            _GRAPH_CANCEL_LOCK,
            graph_cancel_token,
        )
        # AC-05: ephemeral store cleanup. When the call lazily
        # built a fresh index, close the underlying SQLite store
        # so file handles do not accumulate across calls.
        if ephemeral_handle is not None:
            with contextlib.suppress(Exception):
                ephemeral_handle.store.close()
    payload = _graph_result_to_dict(result)
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=False,
    )


def _int_param(params: dict[str, object], key: str, default: int) -> int:
    """Coerce an integer parameter, falling back to ``default`` on error."""
    raw: object = params.get(key, default)
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return default
    return default


def _strict_int_param(
    params: dict[str, object],
    key: str,
    *,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    """Coerce a strictly-bounded integer parameter; fail-closed on bad input.

    AC-05: bounded per-call budgets cannot be silently widened by
    malformed or out-of-range values. ``bool`` is rejected because
    Python treats ``True``/``False`` as ``int``; floats must be
    integer-valued; strings must parse to an integer. Returns the
    supplied ``default`` only when the caller did not provide the
    key at all.
    """
    if key not in params:
        value = default
    else:
        raw: object = params[key]
        if isinstance(raw, bool):
            raise InvalidParamsError(
                f"{key} must be an integer in [{min_value}, {max_value}]"
            )
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, str):
            try:
                value = int(raw)
            except ValueError as exc:
                raise InvalidParamsError(
                    f"{key} must be an integer in [{min_value}, {max_value}]; "
                    f"got {raw!r}"
                ) from exc
        elif isinstance(raw, float):
            if not raw.is_integer():
                raise InvalidParamsError(
                    f"{key} must be an integer in [{min_value}, {max_value}]"
                )
            value = int(raw)
        else:
            raise InvalidParamsError(
                f"{key} must be an integer in [{min_value}, {max_value}]"
            )
    if value < min_value or value > max_value:
        raise InvalidParamsError(
            f"{key} must be an integer in [{min_value}, {max_value}]; got {value}"
        )
    return value


__all__ = [
    "DEFAULT_INDEX_ROOT",
    "ExploreIndex",
    "build_explore_index",
    "handle_ralph_graph",
    "handle_ralph_index_status",
    "handle_ralph_reindex",
]


# --- Minimal unused-import shim for static analyzers ----------------------

# Re-export module-level helpers so tests can import them directly.
__all__ += [
    "_build_reindex_payload",
    "_build_status_payload",
    "_gitignore_coverage",
    "_gitignore_repair_payload",
    "_resolve_explore_index",
    "_resolve_index_dir",
]
