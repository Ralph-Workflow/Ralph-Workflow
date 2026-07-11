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
from typing import TYPE_CHECKING, Final, Protocol, cast

if TYPE_CHECKING:
    from ralph.mcp.explore.store import ExploreStore

from ralph.mcp.explore.pipeline import ReindexOptions, ReindexResult
from ralph.mcp.explore._pipeline_writer import ReindexWriter


class ReindexRunner(Protocol):
    """Structural reindex runner (accepts store + workspace + **kwargs)."""

    def __call__(  # pragma: no cover - structural type
        self,
        store: ExploreStore,
        workspace_root: Path,
        *args: object,
        **kwargs: object,
    ) -> ReindexResult: ...


ReindexOptionsFactory = Callable[[int], ReindexOptions]

logger = logging.getLogger(__name__)


# Default budget for the lifecycle refresh. The pipeline runner wires
# a fresh refresh before AND after each agent invocation; each refresh
# has its own deadline so the agent cannot be starved by slow I/O.
DEFAULT_HOOK_TIMEOUT_MS: Final[int] = 2_000

#: Phase drains whose agent invocations are allowed to trigger an
#: indexed refresh. The set is intentionally narrow — AC-04 only
#: refreshes for explicit development / fix session drains, never for
#: planning, review, commit, or analysis. A phase that is mapped to
#: ``role=execution`` for any other reason (e.g. the planning block
#: inherits the legacy ``execution`` role) must NOT trigger a refresh
#: just because the role matches; the drain is the authoritative
#: identity of a dev/fix session.
REFRESHABLE_PHASE_DRAINS: Final[frozenset[str]] = frozenset({"development", "fix"})


@dataclass(frozen=True, slots=True)
class LifecycleHookResult:
    """Result of a single lifecycle refresh call."""

    invoked: bool
    timed_out: bool
    reindex_result: ReindexResult | None = None
    skipped_reason: str | None = None


def is_execution_phase_for_refresh(
    *,
    phase_role: str | None,
    phase_drain: str | None = None,
) -> bool:
    """Return True when the phase triggers an indexed refresh.

    AC-04: the lifecycle refresh must only run for development / fix
    sessions, never for planning, review, commit, or analysis. The
    ``role`` check alone is too permissive: the planning block in
    ``ralph/policy/defaults/pipeline.toml`` is mapped to
    ``role = "execution"`` while its drain is ``planning``, and
    allowing that drain to trigger a refresh would index the
    workspace for the planning agent — an unrelated and uncosted
    cost. The ``phase_drain`` is therefore the authoritative gate;
    the ``phase_role`` argument is preserved for backward
    compatibility (and as a defensive cross-check) but the drain
    must also be in :data:`REFRESHABLE_PHASE_DRAINS`.
    """
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
    reindex_runner: ReindexRunner | None = None,
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
    reindex_runner: ReindexRunner | None,
    kind: str,
) -> LifecycleHookResult:
    """Run a refresh; swallow exceptions so the agent is never starved."""
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
    # The injected runner takes precedence over the handle's
    # optional ``runner()`` method, which lets tests wire a stub
    # runner without going through pipeline.reindex.
    runner_value: ReindexRunner | None = reindex_runner
    if runner_value is None:
        handle_runner_obj: object = getattr(explore_index, "runner", None)
        if callable(handle_runner_obj):
            resolved_runner: object = handle_runner_obj()
            runner_value = cast("ReindexRunner", resolved_runner)
    if runner_value is None:
        # Default runner: invoke ``claim_reindex`` so the
        # production coordinator handles concurrent calls and
        # dirty-path coalescing. Tests bypass this path with the
        # ``reindex_runner`` injection.
        runner_value = cast("ReindexRunner", claim_reindex)
    options_value: ReindexOptions = ReindexOptions(
        mode="changed", timeout_ms=timeout_ms
    )
    try:
        runner: ReindexRunner = runner_value
        # Backward compat: legacy/test runners accept ``opts=`` while
        # the production ``ReindexWriter.claim``-style runner accepts
        # ``options=``. Detect the keyword name once at runtime and
        # dispatch with the matching name so existing test fixtures
        # (which still use ``opts=``) keep working unchanged.
        kwargs_name = "opts" if reindex_runner is not None else "options"
        raw_result: object = runner(
            store,
            workspace_root,
            **{kwargs_name: options_value},
        )
        result: ReindexResult = raw_result
    except Exception as exc:
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
    result_status: object = getattr(result, "status", "")
    result_status_str: str = (
        result_status if isinstance(result_status, str) else str(result_status)
    )
    timed_out = bool(result_status_str == "timed_out")
    return LifecycleHookResult(
        invoked=True,
        timed_out=timed_out,
        reindex_result=result,
    )


def claim_reindex(
    store: "ExploreStore",
    workspace_root: Path,
    *,
    options: ReindexOptions,
) -> ReindexResult:
    """Default reindex runner for the lifecycle hooks.

    Routes through :meth:`ReindexWriter.claim` so concurrent MCP
    ``ralph_reindex`` calls and lifecycle hooks coalesce into a
    single writer per workspace. Exposed at module scope so the
    coalescing integration test can drive it on its own thread.
    """
    return ReindexWriter.claim(
        store,
        workspace_root=workspace_root,
        options=options,
    )


__all__ = [
    "DEFAULT_HOOK_TIMEOUT_MS",
    "claim_reindex",
    "LifecycleHookResult",
    "after_agent_refresh",
    "before_agent_refresh",
    "is_execution_phase_for_refresh",
]
