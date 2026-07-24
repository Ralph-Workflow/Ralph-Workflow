from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING

from ralph.display.plan_summary import PlanSummary
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.state import PipelineState, RunMetrics

if TYPE_CHECKING:
    from ralph.display.snapshot import PipelineSnapshot

_MAX_NOTIFY_SECONDS = 0.001
PLAN_STEP_COUNT = 2


def _make_state() -> PipelineState:
    return PipelineState(
        phase="development",
        previous_phase=None,
        outer_progress={"iteration": 1},
        budget_caps={"iteration": 5, "reviewer_pass": 2},
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        metrics=RunMetrics(),
        worker_states={},
        work_units=(),
    )


def _make_subscriber(
    *,
    maxsize: int = 0,
    workspace_root: Path | None = None,
    prompt_reader: object = None,
    plan_reader: object = None,
    plan_marker_reader: object = None,
) -> tuple[Queue[PipelineSnapshot], PipelineSubscriber]:
    q: Queue[PipelineSnapshot] = Queue(maxsize=maxsize)
    kwargs: dict = {
        "queue": q,
        "workspace_root": workspace_root or Path("/tmp"),
        "run_id": "test-run",
        "_prompt_path_finder": lambda _root: None,
    }
    if prompt_reader is not None:
        kwargs["_prompt_reader"] = prompt_reader
    if plan_reader is not None:
        kwargs["_plan_reader"] = plan_reader
    if plan_marker_reader is not None:
        kwargs["_plan_marker_reader"] = plan_marker_reader
    sub = PipelineSubscriber(**kwargs)
    return q, sub


class TestPlanArtifactRefresh:
    def test_notify_refreshes_plan_artifact_created_after_init(self) -> None:
        current_plan: list[PlanSummary | None] = [None]
        current_marker: list[int | None] = [None]
        q, sub = _make_subscriber(
            workspace_root=Path("/workspace"),
            plan_reader=lambda _root: current_plan[0],
            plan_marker_reader=lambda _root: current_marker[0],
        )
        state = _make_state()

        sub.notify(state)
        first = q.get_nowait()
        assert first.plan_summary is None

        current_plan[0] = PlanSummary(
            summary="Ship a visible, copy-pasteable planning transcript",
            scope_items=("Render plan", "Show results", "Preserve display behavior"),
            total_steps=PLAN_STEP_COUNT,
            risks_mitigations=("Keep transcript output plain-text safe",),
        )
        current_marker[0] = 1

        sub.notify(state)
        refreshed = q.get_nowait()
        assert refreshed.plan_summary == "Ship a visible, copy-pasteable planning transcript"
        assert refreshed.plan_scope_items == (
            "Render plan",
            "Show results",
            "Preserve display behavior",
        )
        assert refreshed.plan_total_steps == PLAN_STEP_COUNT
        assert refreshed.plan_risks == ("Keep transcript output plain-text safe",)
