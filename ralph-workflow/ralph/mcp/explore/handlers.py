from __future__ import annotations

import contextlib
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


@dataclass
class ExploreIndex:
    workspace_root: Path
    index_root: Path
    store: ExploreStore
    last_refresh_kind: str = "none"
    cold_index_required: bool = False
    generation: int = 0
    last_job_status: str | None = None
    reindex_in_progress: bool = False
    staleness_block_cache: tuple[tuple[int, int, float | None, int], dict[str, object]] | None = (
        None
    )

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
    return Path(workspace_root) / DEFAULT_INDEX_ROOT


def build_explore_index(workspace_root: Path) -> ExploreIndex:
    workspace_root = Path(workspace_root).resolve()
    index_root = _resolve_index_dir(workspace_root)
    store = ExploreStore(index_root)
    from ralph.mcp.explore.pipeline import EXTRACTOR_VERSION
    from ralph.mcp.explore.pipeline import EXTRACTOR_VERSION as STRUCTURE_EXTRACTOR_VERSION
    from ralph.mcp.explore.store import SCHEMA_VERSION

    raw = store.get_setting("current_generation") or "0"
    try:
        generation = int(raw)
    except ValueError:
        generation = 0
    persisted_schema = store.get_setting("schema_version")
    persisted_extractor = store.get_setting("extractor_version")
    persisted_structure = store.get_setting("structure_extractor_version")
    if (
        (persisted_schema is None and store.index_storage_bytes() > 0)
        or (persisted_schema is not None and persisted_schema != SCHEMA_VERSION)
        or (persisted_extractor is not None and persisted_extractor != EXTRACTOR_VERSION)
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
    elif persisted_structure is not None and persisted_structure != STRUCTURE_EXTRACTOR_VERSION:
        with contextlib.suppress(sqlite3.Error, OSError, RuntimeError):
            store._conn.execute("DELETE FROM spans")
            store._conn.execute("DELETE FROM symbols")
            store._conn.execute("DELETE FROM edges")
            store._conn.commit()
    latest_row: sqlite3.Row | None = store.latest_job()
    raw_status: object = row_str(latest_row, "status") if latest_row is not None else ""
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


def _resolve_explore_index(session: object) -> ExploreIndex | None:
    handle: ExploreIndex | None = getattr(session, "explore_index", None)
    if handle is None:
        return None
    return handle


def _gitignore_coverage(workspace_root: Path) -> bool:
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


def _new_cancel_token() -> str:
    return uuid.uuid4().hex


def _arm_cancel_flag(
    registry: dict[str, bool],
    lock: threading.Lock,
    token: str,
    initial: object,
) -> Callable[[], bool]:
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
    with lock:
        registry.pop(token, None)


def _int_param(params: dict[str, object], key: str, default: int) -> int:
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
    if key not in params:
        value = default
    else:
        raw: object = params[key]
        if isinstance(raw, bool):
            raise InvalidParamsError(f"{key} must be an integer in [{min_value}, {max_value}]")
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, str):
            try:
                value = int(raw)
            except ValueError as exc:
                raise InvalidParamsError(
                    f"{key} must be an integer in [{min_value}, {max_value}]; got {raw!r}"
                ) from exc
        elif isinstance(raw, float):
            if not raw.is_integer():
                raise InvalidParamsError(f"{key} must be an integer in [{min_value}, {max_value}]")
            value = int(raw)
        else:
            raise InvalidParamsError(f"{key} must be an integer in [{min_value}, {max_value}]")
    if value < min_value or value > max_value:
        raise InvalidParamsError(
            f"{key} must be an integer in [{min_value}, {max_value}]; got {value}"
        )
    return value


__all__ = [
    "DEFAULT_INDEX_ROOT",
    "ExploreIndex",
    "_arm_cancel_flag",
    "_disarm_cancel_flag",
    "_gitignore_coverage",
    "_int_param",
    "_new_cancel_token",
    "_resolve_explore_index",
    "_resolve_index_dir",
    "_strict_int_param",
    "build_explore_index",
]


def __getattr__(name: str) -> object:
    if name == "graph_module":
        from ralph.mcp.explore import graph as graph_module

        return graph_module
    if name == "_GRAPH_CANCEL_FLAGS":
        from ralph.mcp.explore._handlers_graph import _GRAPH_CANCEL_FLAGS

        return _GRAPH_CANCEL_FLAGS
    if name == "_GRAPH_CANCEL_LOCK":
        from ralph.mcp.explore._handlers_graph import _GRAPH_CANCEL_LOCK

        return _GRAPH_CANCEL_LOCK
    if name == "_graph_edge_to_dict":
        from ralph.mcp.explore._handlers_graph import _graph_edge_to_dict

        return _graph_edge_to_dict
    if name == "_graph_node_to_dict":
        from ralph.mcp.explore._handlers_graph import _graph_node_to_dict

        return _graph_node_to_dict
    if name == "_graph_result_to_dict":
        from ralph.mcp.explore._handlers_graph import _graph_result_to_dict

        return _graph_result_to_dict
    if name == "handle_ralph_graph":
        from ralph.mcp.explore._handlers_graph import handle_ralph_graph

        return handle_ralph_graph
    if name == "_build_disabled_status_payload":
        from ralph.mcp.explore._handlers_index_status import _build_disabled_status_payload

        return _build_disabled_status_payload
    if name == "_build_status_payload":
        from ralph.mcp.explore._handlers_index_status import _build_status_payload

        return _build_status_payload
    if name == "_gitignore_child_rule_present":
        from ralph.mcp.explore._handlers_index_status import _gitignore_child_rule_present

        return _gitignore_child_rule_present
    if name == "_gitignore_coverage":
        from ralph.mcp.explore._handlers_index_status import _gitignore_coverage

        return _gitignore_coverage
    if name == "_gitignore_repair_payload":
        from ralph.mcp.explore._handlers_index_status import _gitignore_repair_payload

        return _gitignore_repair_payload
    if name == "handle_ralph_index_status":
        from ralph.mcp.explore._handlers_index_status import handle_ralph_index_status

        return handle_ralph_index_status
    if name == "_REINDEX_CANCEL_FLAGS":
        from ralph.mcp.explore._handlers_reindex import _REINDEX_CANCEL_FLAGS

        return _REINDEX_CANCEL_FLAGS
    if name == "_REINDEX_CANCEL_LOCK":
        from ralph.mcp.explore._handlers_reindex import _REINDEX_CANCEL_LOCK

        return _REINDEX_CANCEL_LOCK
    if name == "_build_reindex_payload":
        from ralph.mcp.explore._handlers_reindex import _build_reindex_payload

        return _build_reindex_payload
    if name == "handle_ralph_reindex":
        from ralph.mcp.explore._handlers_reindex import handle_ralph_reindex

        return handle_ralph_reindex
    raise AttributeError(f"module 'ralph.mcp.explore.handlers' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(
        {
            "DEFAULT_INDEX_ROOT",
            "ExploreIndex",
            "_GRAPH_CANCEL_FLAGS",
            "_GRAPH_CANCEL_LOCK",
            "_REINDEX_CANCEL_FLAGS",
            "_REINDEX_CANCEL_LOCK",
            "_arm_cancel_flag",
            "_build_disabled_status_payload",
            "_build_reindex_payload",
            "_build_status_payload",
            "_disarm_cancel_flag",
            "_gitignore_child_rule_present",
            "_gitignore_coverage",
            "_gitignore_repair_payload",
            "_graph_edge_to_dict",
            "_graph_node_to_dict",
            "_graph_result_to_dict",
            "_int_param",
            "_new_cancel_token",
            "_resolve_explore_index",
            "_resolve_index_dir",
            "_strict_int_param",
            "build_explore_index",
            "graph_module",
            "handle_ralph_graph",
            "handle_ralph_index_status",
            "handle_ralph_reindex",
        }
    )
