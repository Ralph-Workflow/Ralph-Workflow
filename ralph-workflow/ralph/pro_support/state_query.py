from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, cast

from ralph.pro_support.marker import read_marker_file, read_run_id

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.state import PipelineState


@dataclasses.dataclass(frozen=True, slots=True)
class PipelineStateSnapshot:
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
    latest: PipelineStateSnapshot | None = None

    def publish(self, snapshot: PipelineStateSnapshot) -> None:
        self.latest = dataclasses.replace(
            snapshot,
            metrics=dict(snapshot.metrics),
            budget_caps=dict(snapshot.budget_caps),
            outer_progress=dict(snapshot.outer_progress),
            loop_iterations=dict(snapshot.loop_iterations),
        )

    def get_latest(self) -> PipelineStateSnapshot | None:
        return self.latest


def build_pipeline_state_snapshot(
    state: PipelineState,
    workspace_root: Path | str,
) -> PipelineStateSnapshot:
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
