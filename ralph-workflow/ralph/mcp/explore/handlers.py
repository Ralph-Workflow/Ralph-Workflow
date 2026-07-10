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

import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.explore.pipeline import (
    ReindexOptions,
)
from ralph.mcp.explore.store import (
    DEFAULT_INDEX_DB,
    DEFAULT_INDEX_ROOT,
    ExploreStore,
    row_str,
)
from ralph.mcp.tools.coordination import (
    InvalidParamsError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence



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
    'DEFAULT_INDEX_ROOT',
    'ExploreIndex',
    '_arm_cancel_flag',
    '_disarm_cancel_flag',
    '_gitignore_coverage',
    '_int_param',
    '_new_cancel_token',
    '_resolve_explore_index',
    '_resolve_index_dir',
    '_strict_int_param',
    'build_explore_index',
]


# --- Deferred re-exports (PEP 562) -------------------------------------
#
# The public MCP handlers and their per-sub-module helpers live in
# dedicated sub-modules (``_handlers_graph``,
# ``_handlers_index_status``, ``_handlers_reindex``). Importing those
# sub-modules eagerly at module top-level would form an import cycle
# because each sub-module imports ``ExploreIndex``,
# ``_int_param``/``_strict_int_param``/``_new_cancel_token`` from this
# module. Resolving the symbols lazily through PEP 562
# ``__getattr__`` keeps the public API surface (``from ralph.mcp.explore.handlers
# import handle_ralph_graph``) source-compatible without creating
# the cycle.
_LAZY_REEXPORTS: dict[str, str] = {  # bounded-accumulator-ok: PEP 562 dispatch table; fixed size, populated once at module load
    "graph_module": "ralph.mcp.explore.graph",
    "_GRAPH_CANCEL_FLAGS": "ralph.mcp.explore._handlers_graph",
    "_GRAPH_CANCEL_LOCK": "ralph.mcp.explore._handlers_graph",
    "_graph_edge_to_dict": "ralph.mcp.explore._handlers_graph",
    "_graph_node_to_dict": "ralph.mcp.explore._handlers_graph",
    "_graph_result_to_dict": "ralph.mcp.explore._handlers_graph",
    "handle_ralph_graph": "ralph.mcp.explore._handlers_graph",
    "_build_disabled_status_payload": "ralph.mcp.explore._handlers_index_status",
    "_build_status_payload": "ralph.mcp.explore._handlers_index_status",
    "_gitignore_child_rule_present": "ralph.mcp.explore._handlers_index_status",
    "_gitignore_coverage": "ralph.mcp.explore._handlers_index_status",
    "_gitignore_repair_payload": "ralph.mcp.explore._handlers_index_status",
    "handle_ralph_index_status": "ralph.mcp.explore._handlers_index_status",
    "_REINDEX_CANCEL_FLAGS": "ralph.mcp.explore._handlers_reindex",
    "_REINDEX_CANCEL_LOCK": "ralph.mcp.explore._handlers_reindex",
    "_build_reindex_payload": "ralph.mcp.explore._handlers_reindex",
    "handle_ralph_reindex": "ralph.mcp.explore._handlers_reindex",
}


def __getattr__(name: str) -> object:
    """Resolve ``handlers.<name>`` lazily to a sub-module symbol.

    PEP 562 module-level ``__getattr__``. Mirrors the same pattern
    as :mod:`ralph.mcp.explore.graph` so the per-sub-module public
    surface remains importable through the legacy
    ``ralph.mcp.explore.handlers`` namespace without re-entering
    the explore subgraph and forming a partial-init cycle.
    """
    if name in _LAZY_REEXPORTS:
        # Lazy resolution of the per-sub-module handlers. ``graph_module``
        # is a self-referential alias that resolves to the module
        # itself (the lazy re-export of the graph module); all other
        # names are forwarded as ordinary attribute access on the
        # resolved sub-module.
        import importlib
        import sys

        sub_module = importlib.import_module(_LAZY_REEXPORTS[name])
        if name == "graph_module":
            value: object = sub_module
        else:
            value = getattr(sub_module, name)
        setattr(sys.modules[__name__], name, value)
        return value
    raise AttributeError(
        f"module 'ralph.mcp.explore.handlers' has no attribute {name!r}"
    )


def __dir__() -> list[str]:
    """Include the lazy re-export names in ``dir(handlers)``.

    Without this hook the per-sub-module handlers and helpers are
    invisible to introspection. Keeping the sorted supplementary
    list grouped with the eagerly defined names preserves the
    documented public surface for tooling and auto-import wildcard
    forms used by bridge specs.
    """
    eagerly_defined_names: list[str] = list(globals())  # type: ignore[misc]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
    lazy_rexport_names: list[str] = list(_LAZY_REEXPORTS.keys())
    return sorted(set(eagerly_defined_names) | set(lazy_rexport_names))
