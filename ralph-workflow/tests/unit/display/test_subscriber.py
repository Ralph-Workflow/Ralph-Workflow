from __future__ import annotations

import asyncio
import time
from pathlib import Path
from queue import Queue

from ralph.display.snapshot import DashboardSnapshot
from ralph.display.subscriber import DashboardSubscriber
from ralph.pipeline.state import PipelineState, RunMetrics

_MAX_NOTIFY_SECONDS = 0.001


def _make_state() -> PipelineState:
    return PipelineState(
        phase="development",
        previous_phase=None,
        iteration=1,
        total_iterations=5,
        reviewer_pass=0,
        total_reviewer_passes=2,
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
    prompt_reader=None,
) -> tuple[Queue[DashboardSnapshot], DashboardSubscriber]:
    q: Queue[DashboardSnapshot] = Queue(maxsize=maxsize)
    kwargs: dict = {
        "queue": q,
        "workspace_root": workspace_root or Path("/tmp"),
        "run_id": "test-run",
    }
    if prompt_reader is not None:
        kwargs["prompt_reader"] = prompt_reader
    sub = DashboardSubscriber(**kwargs)
    return q, sub


class TestNotifyEnqueues:
    def test_notify_puts_one_snapshot(self) -> None:
        q, sub = _make_subscriber()
        sub.notify(_make_state())
        assert q.qsize() == 1
        item = q.get_nowait()
        assert isinstance(item, DashboardSnapshot)

    def test_notify_snapshot_has_correct_run_id(self) -> None:
        q: Queue[DashboardSnapshot] = Queue()
        sub = DashboardSubscriber(queue=q, workspace_root=Path("/tmp"), run_id="my-run")
        sub.notify(_make_state())
        snap = q.get_nowait()
        assert snap.run_id == "my-run"


class TestPerformance:
    def test_notify_100_times_each_under_1ms(self) -> None:
        _q, sub = _make_subscriber()
        state = _make_state()
        for _ in range(100):
            start = time.perf_counter()
            sub.notify(state)
            elapsed = time.perf_counter() - start
            assert elapsed < _MAX_NOTIFY_SECONDS, f"notify took {elapsed:.4f}s, expected <1ms"


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


class TestAsyncioCompat:
    def test_notify_from_asyncio_task(self) -> None:
        q, sub = _make_subscriber()
        state = _make_state()

        async def main() -> None:
            sub.notify(state)

        asyncio.run(main())
        assert q.qsize() == 1

    def test_notify_via_asyncio_to_thread(self) -> None:
        q, sub = _make_subscriber()
        state = _make_state()

        async def main() -> None:
            await asyncio.to_thread(sub.notify, state)

        asyncio.run(main())
        assert q.qsize() == 1


class TestPromptCaching:
    def test_fake_prompt_reader_result_in_snapshots(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("hello")

        def fake_reader(p: Path) -> tuple[str, ...]:
            return ("FAKE",)

        q, sub = _make_subscriber(workspace_root=tmp_path, prompt_reader=fake_reader)
        sub.notify(_make_state())
        snap = q.get_nowait()
        assert snap.prompt_preview == ("FAKE",)

    def test_no_prompt_md_gives_empty_preview(self) -> None:
        q, sub = _make_subscriber(workspace_root=Path("/tmp/__no_such_dir__"))
        assert sub._prompt_path is None
        assert sub._prompt_preview == ()
        sub.notify(_make_state())
        snap = q.get_nowait()
        assert snap.prompt_preview == ()

    def test_prompt_reader_called_exactly_once(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("content")

        counter: list[int] = [0]

        def counting_reader(p: Path) -> tuple[str, ...]:
            counter[0] += 1
            return ("cached",)

        _q, sub = _make_subscriber(workspace_root=tmp_path, prompt_reader=counting_reader)
        state = _make_state()
        for _ in range(5):
            sub.notify(state)

        assert counter[0] == 1


class TestQueueProperty:
    def test_queue_property_returns_injected_queue(self) -> None:
        q: Queue[DashboardSnapshot] = Queue()
        sub = DashboardSubscriber(queue=q, workspace_root=Path("/tmp"), run_id="x")
        assert sub.queue is q
