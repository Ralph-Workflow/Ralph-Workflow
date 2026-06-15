"""Read-only pipeline state observability for Pro.

Pro can monitor the engine's progress by reading a structured
snapshot of the live pipeline state on every reduce step. The
snapshot is a frozen, read-only view: the live ``PipelineState``
MUST remain mutable for the engine, and a Pro consumer of the
snapshot MUST NOT be able to mutate engine state through the
snapshot.

Design constraints (enforced by ``make verify``):

- **Frozen dataclass with primitive copies.** Snapshot fields are
  ``str``, ``int``, ``bool``, or shallow-copied ``dict`` fields;
  the live ``PipelineState`` is never referenced from the
  snapshot.
- **Plain ``dict`` for nested mapping fields.** ``metrics`` is a
  pydantic ``RunMetrics.model_dump()`` (plain dict), and
  ``outer_progress`` / ``loop_iterations`` / ``budget_caps`` are
  shallow ``dict`` copies.
- **No ``time.sleep`` in production.** The publish is a constant
  time operation.

The publish happens inside ``_run_inner_loop`` (after
``state = step_result``) so the snapshot is always taken AFTER
the runner has updated the state but BEFORE the next iteration
of the loop. This matches the contract: Pro can poll the
registry's ``get_latest()`` at any time and see the most recent
state.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, cast

from ralph.pro_support.marker import read_marker_file, read_run_id

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.state import PipelineState


@dataclasses.dataclass(frozen=True, slots=True)
class PipelineStateSnapshot:
    """Frozen, read-only view of the live pipeline state.

    All mapping fields are shallow copies of the corresponding
    state fields; the snapshot holds no reference to the live
    ``PipelineState``. The live state remains mutable for the
    engine.
    """

    phase: str
    previous_phase: str | None
    run_id: str | None
    interrupted_by_user: bool
    last_error: str | None
    metrics: dict[str, int]
    budget_caps: dict[str, int]
    outer_progress: dict[str, int]
    loop_iterations: dict[str, int]
    iteration: int
    analysis_iteration: int

    def __post_init__(self) -> None:
        for value in (
            self.metrics,
            self.budget_caps,
            self.outer_progress,
            self.loop_iterations,
        ):
            if not isinstance(value, dict):
                raise TypeError(
                    f"PipelineStateSnapshot mapping field must be a plain dict, "
                    f"got {type(value).__name__}"
                )


@dataclasses.dataclass
class SnapshotRegistry:
    """Mutable holder for the most-recent ``PipelineStateSnapshot``.

    The pipeline publishes to this registry on each reduce step.
    Pro consumers call ``get_latest()`` to read the current state.
    """

    latest: PipelineStateSnapshot | None = None

    def publish(self, snapshot: PipelineStateSnapshot) -> None:
        """Store the most-recent snapshot. Idempotent: replaces prior value.

        Stores a field-by-field copy of the supplied snapshot so
        that ``get_latest()`` returns an equal but NOT identical
        instance. This is a defensive copy: the publish call site
        is trusted, but a future regression that mutated the
        stored snapshot would not silently corrupt the registry.
        """
        self.latest = dataclasses.replace(
            snapshot,
            metrics=dict(snapshot.metrics),
            budget_caps=dict(snapshot.budget_caps),
            outer_progress=dict(snapshot.outer_progress),
            loop_iterations=dict(snapshot.loop_iterations),
        )

    def get_latest(self) -> PipelineStateSnapshot | None:
        """Return the most-recent snapshot, or ``None`` if none has been published."""
        return self.latest


def build_pipeline_state_snapshot(
    state: PipelineState,
    workspace_root: Path | str,
) -> PipelineStateSnapshot:
    """Build a read-only snapshot of the live ``PipelineState``.

    Args:
        state: The live, mutable ``PipelineState``.
        workspace_root: The workspace root used to resolve the
            ``run_id`` from the marker file. When the marker is
            missing, ``run_id`` is ``None``.
    """
    marker = read_marker_file(workspace_root)
    run_id = read_run_id(marker)
    previous_phase: str | None = (
        str(state.previous_phase) if state.previous_phase is not None else None
    )
    return PipelineStateSnapshot(
        phase=str(state.phase),
        previous_phase=previous_phase,
        run_id=run_id,
        interrupted_by_user=state.interrupted_by_user,
        last_error=state.last_error,
        metrics={k: int(cast("int", v)) for k, v in state.metrics.model_dump().items()},
        budget_caps=dict(state.budget_caps),
        outer_progress=dict(state.outer_progress),
        loop_iterations=dict(state.loop_iterations),
        iteration=state.outer_progress.get("iteration", 0),
        analysis_iteration=state.loop_iterations.get("analysis_iteration", 0),
    )


__all__ = [
    "PipelineStateSnapshot",
    "SnapshotRegistry",
    "build_pipeline_state_snapshot",
]
