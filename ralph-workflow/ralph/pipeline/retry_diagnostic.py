"""Public surface of the integration-retry and diagnostic-budget path.

The retry path answers one question after a failed integration attempt:
does Ralph keep waiting, re-derive live state, hand the conflict to the
automated resolver, or escalate into the autonomous fallback ladder (the
typed "diagnostic budget spent" outcome)?  The decision itself lives in
:mod:`ralph.pipeline.run_loop`; this module is the supported entry point
for driving it from outside that module, plus the injectable
collaborators the path uses.

Public surface
--------------

* :func:`wait_for_integration_retry` -- run one integration-retry
  iteration and return the resulting
  :class:`~ralph.pipeline.state.PipelineState`.
* :func:`run_owned_integration_conflict_resolution` -- run the
  budget-guarded conflict resolver against live blocking evidence.
* :class:`RetryDiagnosticHooks` -- the injectable collaborators the retry
  path uses.
* :data:`EVIDENCE_LEASE_SECONDS` -- how long a durable diagnostic-evidence
  lease stays valid before the retry path treats it as expired.

Why the hooks exist
-------------------

The retry path reaches five collaborators that live outside it: the
checkpoint writer, the automated-resolver handoff, the auto-integrate
recovery preamble, the conflict resolver, and the live-state re-deriver.
They are exactly the seams a caller that owns the surrounding resources
(the inner loop, an embedding process, a harness that drives one retry
iteration) needs to substitute.  Exposing them as an injected,
documented collaborator keeps the retry path observable through a public
surface instead of through module internals.

Every hook field defaults to ``None``, which means "use the production
implementation".  Production code never sets a hook; it constructs the
context and lets the defaults apply.  A caller that supplies a hook
replaces exactly one collaborator and inherits the production behaviour
of every other one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pipeline._integration_diagnostic_evidence import EVIDENCE_LEASE_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.mcp.artifacts.file_backend import FileBackend
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.pipeline.run_loop import LoopContext
    from ralph.pipeline.state import PipelineState
    from ralph.workspace.scope import WorkspaceScope

__all__ = [
    "EVIDENCE_LEASE_SECONDS",
    "RetryDiagnosticHooks",
    "run_owned_integration_conflict_resolution",
    "wait_for_integration_retry",
]


@dataclass
class RetryDiagnosticHooks:
    """Injectable collaborators for the integration-retry path.

    Every field is optional.  ``None`` means "use the production
    implementation"; a non-``None`` value replaces exactly one
    collaborator of the retry path and nothing else.  The dataclass is
    mutable so a caller can adjust a single collaborator after
    construction.
    """

    # Persists the recovered rebase state to the run checkpoint.
    save_checkpoint: Callable[[PipelineState, LoopContext], None] | None = None
    # Dispatches the automated resolver handoff for a retained failure.
    handoff_retained_failure: Callable[[LoopContext, str], None] | None = None
    # Re-derives live integration state from Git (the recovery preamble).
    recovery_preamble: (
        Callable[[WorkspaceScope, UnifiedConfig | None], RebaseState | None] | None
    ) = None
    # Runs the budget-guarded conflict resolver.
    conflict_resolution: Callable[[LoopContext, RebaseState | None], bool] | None = None
    # Resolves the effective consecutive-conflict counter cap.
    effective_conflict_cap: Callable[[LoopContext], int] | None = None
    # Reports whether a paused rebase is on disk at the workspace root.
    paused_rebase_at: Callable[[Path], bool] | None = None
    # Finishes an in-progress merge with the given conflict resolver.
    complete_in_progress_merge: Callable[[Path, str, ConflictResolver], bool] | None = None
    # Resolves a conflicted index that no rebase or merge owns.
    resolve_orphaned_unmerged_index: Callable[[LoopContext, str], bool] | None = None
    # Re-derives the live conflict state from Git.
    rederive_live_conflict_state: Callable[[Path, str, RebaseState], RebaseState | None] | None = (
        None
    )


def wait_for_integration_retry(
    state: PipelineState,
    ctx: LoopContext,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> PipelineState:
    """Run one integration-retry iteration and return the resulting state.

    The retry iteration decides whether the run waits, re-derives live
    state, escalates into the autonomous fallback ladder, or returns
    immediately after clearing durable diagnostic evidence. The returned
    state carries the decision; the durable side effects
    (checkpoint, record) are written by the path itself.
    """
    from ralph.pipeline.run_loop import _wait_for_integration_retry

    return _wait_for_integration_retry(state, ctx, backend=backend)


def run_owned_integration_conflict_resolution(
    ctx: LoopContext,
    rebase: RebaseState | None = None,
) -> bool:
    """Run the budget-guarded conflict resolver against live evidence.

    Returns ``True`` when the resolver cleared the blocking evidence and
    ``False`` when the conflict is unresolved, the budget is spent, or no
    resolver could be run.  Never raises.
    """
    from ralph.pipeline.run_loop import _run_owned_integration_conflict_resolution

    return _run_owned_integration_conflict_resolution(ctx, rebase)
