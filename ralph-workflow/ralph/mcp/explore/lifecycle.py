from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from ralph.mcp.explore._pipeline_writer import ReindexWriter
from ralph.mcp.explore.pipeline import ReindexOptions, ReindexResult
from ralph.workspace.awareness import awareness_for_workspace

if TYPE_CHECKING:
    from ralph.mcp.explore.store import ExploreStore


class ReindexRunner(Protocol):
    def __call__(
        self,
        store: ExploreStore,
        workspace_root: Path,
        *args: object,
        **kwargs: object,
    ) -> ReindexResult: ...


def _coerce_reindex_runner(value: object) -> ReindexRunner | None:
    if not callable(value):
        return None

    def _runner(
        store: ExploreStore,
        workspace_root: Path,
        *args: object,
        **kwargs: object,
    ) -> ReindexResult:
        options = kwargs.get("options")
        if options is None:
            options = kwargs.get("opts")
        if options is None and args:
            options = args[0]
        if options is None:
            msg = "reindex runner requires options"
            raise TypeError(msg)
        raw = value(store, workspace_root, options=options)
        if isinstance(raw, ReindexResult):
            return raw
        msg = f"reindex runner returned {type(raw).__name__}, expected ReindexResult"
        raise TypeError(msg)

    return _runner


ReindexOptionsFactory = Callable[[int], ReindexOptions]

logger = logging.getLogger(__name__)


DEFAULT_HOOK_TIMEOUT_MS: Final[int] = 2_000

REFRESHABLE_PHASE_DRAINS: Final[frozenset[str]] = frozenset({"development", "fix"})


@dataclass(frozen=True, slots=True)
class LifecycleHookResult:
    invoked: bool
    timed_out: bool
    reindex_result: ReindexResult | None = None
    skipped_reason: str | None = None


def is_execution_phase_for_refresh(
    *,
    phase_role: str | None,
    phase_drain: str | None = None,
) -> bool:
    if phase_role != "execution":
        return False
    return phase_drain in REFRESHABLE_PHASE_DRAINS


def before_agent_refresh(
    *,
    workspace_root: Path,
    explore_index: object | None,
    timeout_ms: int = DEFAULT_HOOK_TIMEOUT_MS,
    reindex_runner: ReindexRunner | None = None,
) -> LifecycleHookResult:
    return _run_hook(
        workspace_root=workspace_root,
        explore_index=explore_index,
        timeout_ms=timeout_ms,
        reindex_runner=reindex_runner,
        kind="before",
    )


def after_agent_refresh(
    *,
    workspace_root: Path,
    explore_index: object | None,
    timeout_ms: int = DEFAULT_HOOK_TIMEOUT_MS,
    reindex_runner: ReindexRunner | None = None,
) -> LifecycleHookResult:
    return _run_hook(
        workspace_root=workspace_root,
        explore_index=explore_index,
        timeout_ms=timeout_ms,
        reindex_runner=reindex_runner,
        kind="after",
    )


def _run_hook(
    *,
    workspace_root: Path,
    explore_index: object | None,
    timeout_ms: int,
    reindex_runner: ReindexRunner | None,
    kind: str,
) -> LifecycleHookResult:
    if explore_index is None:
        return LifecycleHookResult(
            invoked=False,
            timed_out=False,
            skipped_reason="explore_index_disabled",
        )
    store: ExploreStore | None = getattr(explore_index, "store", None)
    if store is None:
        return LifecycleHookResult(
            invoked=False,
            timed_out=False,
            skipped_reason="no_store",
        )
    awareness = awareness_for_workspace(workspace_root)
    observed_paths = awareness.drain()
    if observed_paths:
        marker: object = getattr(explore_index, "mark_dirty", None)
        if callable(marker):
            try:
                marker(observed_paths, source_tool="workspace_awareness", reason="observed")
            except (
                OSError,
                RuntimeError,
                ValueError,
                KeyError,
                AttributeError,
                TypeError,
                sqlite3.Error,
            ):
                awareness.requeue(observed_paths, cause="dirty_handoff_failed")
        else:
            awareness.requeue(observed_paths, cause="dirty_handoff_unavailable")
    runner_value: ReindexRunner | None = reindex_runner
    if runner_value is None:
        handle_runner_obj: object = getattr(explore_index, "runner", None)
        if callable(handle_runner_obj):
            runner_value = _coerce_reindex_runner(handle_runner_obj())
    if runner_value is None:
        runner_value = claim_reindex
    options_value: ReindexOptions = ReindexOptions(mode="changed", timeout_ms=timeout_ms)
    try:
        runner: ReindexRunner = runner_value
        kwargs_name = "opts" if reindex_runner is not None else "options"
        raw_result: object = runner(
            store,
            workspace_root,
            **{kwargs_name: options_value},
        )
        if not isinstance(raw_result, ReindexResult):
            msg = f"reindex runner returned {type(raw_result).__name__}, expected ReindexResult"
            raise TypeError(msg)
        result = raw_result
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        AttributeError,
        TypeError,
        sqlite3.Error,
        TimeoutError,
    ) as exc:
        logger.warning(
            "Lifecycle refresh (%s) failed: %s",
            kind,
            exc,
        )
        awareness.set_freshness("partial", cause=f"refresh_{type(exc).__name__}")
        return LifecycleHookResult(
            invoked=True,
            timed_out=False,
            reindex_result=None,
            skipped_reason=f"error:{type(exc).__name__}",
        )
    result_status: object = getattr(result, "status", "")
    result_status_str: str = result_status if isinstance(result_status, str) else str(result_status)
    timed_out = bool(result_status_str == "timed_out")
    if result_status_str == "ok":
        awareness.set_freshness("current")
    elif result_status_str == "timed_out":
        awareness.set_freshness("stale", cause="refresh_timed_out")
    else:
        awareness.set_freshness("partial", cause=f"refresh_{result_status_str or 'failed'}")
    return LifecycleHookResult(
        invoked=True,
        timed_out=timed_out,
        reindex_result=result,
    )


def claim_reindex(
    store: ExploreStore,
    workspace_root: Path,
    *,
    options: ReindexOptions,
) -> ReindexResult:
    return ReindexWriter.claim(
        store,
        workspace_root=workspace_root,
        options=options,
    )


__all__ = [
    "DEFAULT_HOOK_TIMEOUT_MS",
    "LifecycleHookResult",
    "after_agent_refresh",
    "before_agent_refresh",
    "claim_reindex",
    "is_execution_phase_for_refresh",
]
