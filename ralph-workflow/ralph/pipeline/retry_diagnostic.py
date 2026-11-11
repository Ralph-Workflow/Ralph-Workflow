
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

    save_checkpoint: Callable[[PipelineState, LoopContext], None] | None = None
    handoff_retained_failure: Callable[[LoopContext, str], None] | None = None
    recovery_preamble: (
        Callable[[WorkspaceScope, UnifiedConfig | None], RebaseState | None] | None
    ) = None
    conflict_resolution: Callable[[LoopContext, RebaseState | None], bool] | None = None
    paused_rebase_at: Callable[[Path], bool] | None = None
    complete_in_progress_merge: Callable[[Path, str, ConflictResolver], bool] | None = None
    resolve_orphaned_unmerged_index: Callable[[LoopContext, str], bool] | None = None
    rederive_live_conflict_state: Callable[[Path, str, RebaseState], RebaseState | None] | None = (
        None
    )


def wait_for_integration_retry(
    state: PipelineState,
    ctx: LoopContext,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> PipelineState:
    from ralph.pipeline.run_loop import _wait_for_integration_retry

    return _wait_for_integration_retry(state, ctx, backend=backend)


def run_owned_integration_conflict_resolution(
    ctx: LoopContext,
    rebase: RebaseState | None = None,
) -> bool:
    from ralph.pipeline.run_loop import _run_owned_integration_conflict_resolution

    return _run_owned_integration_conflict_resolution(ctx, rebase)
