from __future__ import annotations

from io import StringIO
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING

from loguru import logger

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


class TestBackpressure:
    def test_queue_full_drops_snapshot_silently(self) -> None:
        maxsize = 2
        q, sub = _make_subscriber(maxsize=maxsize)
        state = _make_state()
        for _ in range(3):
            sub.notify(state)
        assert q.qsize() == maxsize
        assert sub.dropped_count == 1

    def test_queue_full_does_not_raise(self) -> None:
        _q, sub = _make_subscriber(maxsize=1)
        state = _make_state()
        sub.notify(state)
        sub.notify(state)

    def test_dropped_count_accumulates(self) -> None:
        maxsize = 2
        notify_count = 10
        q, sub = _make_subscriber(maxsize=maxsize)
        state = _make_state()
        for _ in range(notify_count):
            sub.notify(state)
        assert sub.dropped_count == notify_count - maxsize
        assert q.qsize() == maxsize

    def test_queue_full_does_not_emit_noisy_drop_debug_log(self) -> None:
        stream = StringIO()
        sink_id = logger.add(stream, level="DEBUG", format="{message}")
        try:
            _q, sub = _make_subscriber(maxsize=1)
            state = _make_state()
            sub.notify(state)
            sub.notify(state)
        finally:
            logger.remove(sink_id)

        assert "snapshot dropped" not in stream.getvalue()
