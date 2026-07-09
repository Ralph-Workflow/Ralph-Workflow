"""Before/after dev-fix session lifecycle refresh hooks.

These hooks are invoked by the pipeline runner around agent
invocations for execution-role phases whose session drain is
development or fix. They run a bounded changed-file refresh:

* BEFORE the agent runs, so the agent starts with a fresh-enough
  index.
* AFTER the agent runs (success or exit), so downstream review /
  analysis / commit prompt materialization consumes indexed context.

The hooks must:

* Skip cleanly (no-op) when the explore index is missing or disabled.
* Enforce a strict timeout that is fail-closed for the job
  (``status='timed_out'``) and fail-open for the agent (the agent is
  never blocked indefinitely).
* Coalesce with any in-flight reindex.

Phase 1 keeps the contract narrow: the hooks touch only ``reindex``
and the existing ``pipeline_deps.explore_index`` seam. Phase 2 (Phase
3 in the plan) will add richer hooks for impact-aware editing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from ralph.mcp.explore.pipeline import ReindexResult

logger = logging.getLogger(__name__)


# Default budget for the lifecycle refresh. The pipeline runner wires
# a fresh refresh before AND after each agent invocation; each refresh
# has its own deadline so the agent cannot be starved by slow I/O.
DEFAULT_HOOK_TIMEOUT_MS: Final[int] = 2_000


@dataclass(frozen=True, slots=True)
class LifecycleHookResult:
    """Result of a single lifecycle refresh call."""

    invoked: bool
    timed_out: bool
    reindex_result: "ReindexResult | None" = None
    skipped_reason: str | None = None


def is_execution_phase_for_refresh(*, phase_role: str | None) -> bool:
    """Return True when the phase role triggers an indexed refresh.

    Phase 1 only refreshes for explicit ``execution`` roles whose
    drain is development or fix. The function intentionally accepts
    the role string rather than a phase enum so it stays decoupled
    from the policy package (and is easy to test).
    """
    return phase_role == "execution"


def before_agent_refresh(
    *,
    workspace_root: Path,
    explore_index: object | None,
    timeout_ms: int = DEFAULT_HOOK_TIMEOUT_MS,
    reindex_runner: Callable[..., "ReindexResult"] | None = None,
) -> LifecycleHookResult:
    """Run a bounded changed-file refresh before an agent invocation.

    Skips cleanly when ``explore_index`` is ``None`` or when the
    underlying store is missing. The hook never blocks the agent
    indefinitely; a timeout records ``timed_out=True`` and returns
    a stale-but-usable result.
    """
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
    reindex_runner: Callable[..., "ReindexResult"] | None = None,
) -> LifecycleHookResult:
    """Run a bounded changed-file refresh after an agent invocation."""
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
    reindex_runner: Callable[..., "ReindexResult"] | None,
    kind: str,
) -> LifecycleHookResult:
    """Run a refresh; swallow exceptions so the agent is never starved."""
    if explore_index is None:
        return LifecycleHookResult(
            invoked=False,
            timed_out=False,
            skipped_reason="explore_index_disabled",
        )
    store = getattr(explore_index, "store", None)
    if store is None:
        return LifecycleHookResult(
            invoked=False,
            timed_out=False,
            skipped_reason="no_store",
        )
    # The injected runner takes precedence over the handle's
    # optional ``runner()`` method, which lets tests wire a stub
    # runner without going through pipeline.reindex.
    runner = reindex_runner
    if runner is None:
        handle_runner = getattr(explore_index, "runner", None)
        if callable(handle_runner):
            runner = handle_runner()
    if runner is None:
        # Default runner: invoke the pipeline.reindex function with
        # the live ``store`` and a short timeout. The runner is
        # injected in tests so the hook stays decoupled from the
        # concrete reindex implementation.
        from ralph.mcp.explore.pipeline import reindex as _reindex

        runner = _reindex
    opts_factory = getattr(explore_index, "build_options", None)
    if callable(opts_factory):
        options = opts_factory(timeout_ms=timeout_ms)
    else:
        from ralph.mcp.explore.pipeline import ReindexOptions

        options = ReindexOptions(mode="changed", timeout_ms=timeout_ms)
    try:
        if reindex_runner is None and runner is not None and getattr(runner, "__name__", "") == "reindex":
            result = runner(store, workspace_root, options=options)
        else:
            result = runner(store, workspace_root, opts=options)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Lifecycle refresh (%s) failed: %s",
            kind,
            exc,
        )
        return LifecycleHookResult(
            invoked=True,
            timed_out=False,
            reindex_result=None,
            skipped_reason=f"error:{type(exc).__name__}",
        )
    timed_out = bool(getattr(result, "status", "") == "timed_out")
    return LifecycleHookResult(
        invoked=True,
        timed_out=timed_out,
        reindex_result=result,
    )


__all__ = [
    "DEFAULT_HOOK_TIMEOUT_MS",
    "LifecycleHookResult",
    "after_agent_refresh",
    "before_agent_refresh",
    "is_execution_phase_for_refresh",
]