from __future__ import annotations

from pathlib import Path
from queue import Queue

from ralph.display.snapshot import PipelineSnapshot
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.state import PipelineState, RunMetrics

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


class TestNotifyEnqueues:
    def test_notify_puts_one_snapshot(self) -> None:
        q, sub = _make_subscriber()
        sub.notify(_make_state())
        assert q.qsize() == 1
        item = q.get_nowait()
        assert isinstance(item, PipelineSnapshot)

    def test_notify_snapshot_has_correct_run_id(self) -> None:
        q: Queue[PipelineSnapshot] = Queue()
        sub = PipelineSubscriber(queue=q, workspace_root=Path("/tmp"), run_id="my-run")
        sub.notify(_make_state())
        snap = q.get_nowait()
        assert snap.run_id == "my-run"
