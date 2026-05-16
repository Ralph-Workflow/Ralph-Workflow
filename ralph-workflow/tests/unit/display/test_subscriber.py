from __future__ import annotations

import asyncio
import json
import time
from io import StringIO
from pathlib import Path
from queue import Queue

from loguru import logger

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
        kwargs["prompt_reader"] = prompt_reader
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


class TestPlanArtifactRefresh:
    def test_notify_refreshes_plan_artifact_created_after_init(self, tmp_path: Path) -> None:
        q, sub = _make_subscriber(workspace_root=tmp_path)
        state = _make_state()

        sub.notify(state)
        first = q.get_nowait()
        assert first.plan_summary is None

        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "plan.json").write_text(
            json.dumps(
                {
                    "content": {
                        "summary": {
                            "context": "Ship a visible, copy-pasteable planning transcript",
                            "scope_items": ["Render plan", "Show results"],
                        },
                        "steps": [{"title": "one"}, {"title": "two"}],
                        "risks_mitigations": ["Keep transcript output plain-text safe"],
                    }
                }
            ),
            encoding="utf-8",
        )

        sub.notify(state)
        refreshed = q.get_nowait()
        assert refreshed.plan_summary == "Ship a visible, copy-pasteable planning transcript"
        assert refreshed.plan_scope_items == ("Render plan", "Show results")
        assert refreshed.plan_total_steps == PLAN_STEP_COUNT
        assert refreshed.plan_risks == ("Keep transcript output plain-text safe",)


class TestQueueProperty:
    def test_queue_property_returns_injected_queue(self) -> None:
        q: Queue[PipelineSnapshot] = Queue()
        sub = PipelineSubscriber(queue=q, workspace_root=Path("/tmp"), run_id="x")
        assert sub.queue is q
