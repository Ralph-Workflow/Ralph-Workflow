from __future__ import annotations

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
    prompt_reader: object = None,
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
