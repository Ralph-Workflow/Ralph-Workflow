"""Completion-summary helpers for pipeline phase transitions."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.display.completion_summary import CompletionSummaryOptions
from ralph.display.parallel_display import (
    ParallelDisplay,
    get_display_context,
    resolve_active_display,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.context import DisplayContext
    from ralph.display.snapshot import PipelineSnapshot, SnapshotContext
    from ralph.display.subscriber import PipelineSubscriber
    from ralph.pipeline.state import PipelineState


if TYPE_CHECKING:

    class _ParallelDisplayModule(Protocol):
        ParallelDisplay: type[ParallelDisplay]

    class _SnapshotFromStateFn(Protocol):
        def __call__(
            self,
            state: PipelineState,
            context: SnapshotContext = ...,
        ) -> PipelineSnapshot: ...

    class _SnapshotModule(Protocol):
        snapshot_from_state: _SnapshotFromStateFn


def _snapshot_from_state_func() -> _SnapshotFromStateFn:
    module = cast("_SnapshotModule", import_module("ralph.display.snapshot"))
    return module.snapshot_from_state


def emit_final_summary(
    state: PipelineState,
    workspace_root: Path,
    *,
    subscriber: PipelineSubscriber | None = None,
    display: ParallelDisplay | None = None,
    display_context: DisplayContext,
) -> None:
    """Emit an end-of-run completion summary panel."""
    try:
        snapshot_from_state = _snapshot_from_state_func()

        dropped_count = 0
        snapshot: PipelineSnapshot | None = None
        if subscriber is not None:
            try:
                dropped_count = subscriber.dropped_count
                snapshot = subscriber.build_snapshot(state)
            except Exception:
                logger.debug(
                    "subscriber.build_snapshot failed; falling back to raw snapshot",
                    exc_info=True,
                )
        if snapshot is None:
            snapshot = snapshot_from_state(state)
        pipeline_policy = subscriber.pipeline_policy if subscriber is not None else None
        ctx = get_display_context(display, display_context)
        if isinstance(display, ParallelDisplay):
            pr = display._plain_renderer
            content_block_count: int = pr.content_blocks_count
            thinking_block_count: int = pr.thinking_blocks_count
            tool_call_count: int = pr.tool_calls_count
            error_count: int = pr.errors_count
            elapsed_seconds: float | None = pr.run_elapsed_seconds
        else:
            content_block_count = 0
            thinking_block_count = 0
            tool_call_count = 0
            error_count = 0
            elapsed_seconds = None
        cs_opts = CompletionSummaryOptions(
            workspace_root=workspace_root,
            dropped_count=dropped_count,
            include_context_sections=not state.interrupted_by_user,
            content_block_count=content_block_count,
            thinking_block_count=thinking_block_count,
            tool_call_count=tool_call_count,
            error_count=error_count,
            elapsed_seconds=elapsed_seconds,
            pipeline_policy=pipeline_policy,
        )
        # Route the end-of-run completion panel through the consolidated
        # ParallelDisplay surface (37th emit_* method). When a ParallelDisplay
        # is in scope we call directly; otherwise we resolve one from the
        # DisplayContext so the panel is rendered through the same console
        # and theme as the rest of the transcript.
        target_display = display if isinstance(display, ParallelDisplay) else None
        if target_display is None:
            target_display = resolve_active_display(None, ctx)
        target_display.emit_completion_summary_panel(snapshot, options=cs_opts)
    except Exception:
        logger.debug("Failed to emit completion summary", exc_info=True)
