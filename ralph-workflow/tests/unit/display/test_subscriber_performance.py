from __future__ import annotations

import time
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING

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
    prompt_reader: object=None,
) -> tuple[Queue[PipelineSnapshot], PipelineSubscriber]:
    q: Queue[PipelineSnapshot] = Queue(maxsize=maxsize)
    kwargs: dict = {
        "queue": q,
        "workspace_root": workspace_root or Path("/tmp"),
        "run_id": "test-run",
    }
    if prompt_reader is not None:
        kwargs["_prompt_reader"] = prompt_reader
    sub = PipelineSubscriber(**kwargs)
    return q, sub


class TestPerformance:
    def test_notify_100_times_average_under_1ms(self) -> None:
        _q, sub = _make_subscriber()
        state = _make_state()

        # Warm the cached prompt/plan/analysis paths so we measure steady-state notify cost,
        # not first-call setup or coverage tracer startup noise.
        sub.notify(state)

        start = time.perf_counter()
        for _ in range(100):
            sub.notify(state)
        average_elapsed = (time.perf_counter() - start) / 100

        assert average_elapsed < _MAX_NOTIFY_SECONDS, (
            f"notify averaged {average_elapsed:.4f}s, expected <1ms"
        )
