"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

import asyncio
import subprocess as _subprocess
from typing import TYPE_CHECKING

from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.work_units import (
    WorkUnit,
)
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ralph.pipeline.worker_state import WorkerStatus


def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )


class TestRunnerNoMergeStep:
    def test_runner_fanout_emits_no_branch_or_worktree_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Runner fan-out must never issue git branch/merge/checkout/worktree subprocesses."""

        banned_calls: list[str] = []

        class _RecordingPopen(_subprocess.Popen):
            def __init__(self, cmd: object, *args: object, **kwargs: object) -> None:
                cmd_str = " ".join(str(c) for c in cmd) if not isinstance(cmd, str) else cmd
                banned_calls.extend(
                    cmd_str
                    for banned in ("git branch", "git merge", "git checkout", "git worktree")
                    if banned in cmd_str
                )
                super().__init__(cmd, *args, **kwargs)

        monkeypatch.setattr(_subprocess, "Popen", _RecordingPopen)

        unit = _make_unit("unit-runner", ["src/runner"])
        effect = FanOutEffect(work_units=(unit,), max_workers=1)

        class _FakeDisplay(ParallelDisplay):
            def __init__(self) -> None:
                pass

            def emit(self, unit_id: str, line: str) -> None:
                pass

            def set_status(self, unit_id: str, status: WorkerStatus) -> None:
                pass

        asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(
                    {"unit-runner": FakeRun(outputs=["ok"], exit_code=0, duration_ms=1)}
                ),
                display=_FakeDisplay(),
            )
        )

        assert banned_calls == [], (
            f"Runner fan-out must not issue git branch/merge/checkout/worktree: {banned_calls}"
        )

    def test_runner_event_stream_has_no_merge_or_worktree_events(self, tmp_path: Path) -> None:
        """Event stream from runner fan-out must not contain merge, worktree, or branch events."""

        unit = _make_unit("unit-ev", ["src/ev"])
        effect = FanOutEffect(work_units=(unit,), max_workers=1)

        class _FakeDisplay(ParallelDisplay):
            def __init__(self) -> None:
                pass

            def emit(self, unit_id: str, line: str) -> None:
                pass

            def set_status(self, unit_id: str, status: WorkerStatus) -> None:
                pass

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=FakeAgentExecutor(
                    {"unit-ev": FakeRun(outputs=["ok"], exit_code=0, duration_ms=1)}
                ),
                display=_FakeDisplay(),
            )
        )

        denied = ("Merge", "Worktree", "BranchCreated", "BranchMerged", "Rebase")
        violations = [
            repr(ev)
            for ev in events
            if any(tok in type(ev).__name__ or tok in repr(ev) for tok in denied)
        ]
        assert violations == [], (
            "Runner fan-out event stream must not contain "
            f"merge/worktree/branch events: {violations}"
        )
