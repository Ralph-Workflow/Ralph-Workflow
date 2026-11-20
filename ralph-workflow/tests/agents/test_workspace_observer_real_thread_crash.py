from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest
from watchdog.observers import inotify_c

from ralph.agents.invoke import WorkspaceMonitor, WorkspaceWatchSeams
from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.subprocess_e2e

_WAIT_SECONDS = 5.0
_POLL_SECONDS = 0.05
_RACE_PATH = b"/workspace/ralph-workflow/tmp/ralph-pytest-shards/shard-5/repo"


def _wait_until(predicate: Callable[[], bool]) -> bool:
    deadline = time.monotonic() + _WAIT_SECONDS
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(_POLL_SECONDS)
    return predicate()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
@pytest.mark.timeout_seconds(15)
def test_the_real_inotify_thread_killed_by_the_deleted_directory_race_is_restarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crashes: list[int] = []
    original = inotify_c.Inotify.read_events

    def read_events(self: object, *args: object, **kwargs: object) -> list[object]:
        if not crashes:
            crashes.append(threading.get_ident())
            raise KeyError(_RACE_PATH)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(inotify_c.Inotify, "read_events", read_events)
    source = tmp_path / "app.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    monitor = WorkspaceMonitor(
        tmp_path, seams=WorkspaceWatchSeams(observer_restart_sleep=lambda _seconds: None)
    )
    monitor.start()
    try:
        assert _wait_until(lambda: len(crashes) == 1)
        edits: list[int] = []

        def edit_and_observe() -> bool:
            edits.append(len(edits))
            source.write_text(f"print({len(edits)})\n", encoding="utf-8")
            time.sleep(_POLL_SECONDS)
            count = awareness_for_workspace(tmp_path).snapshot()["dirty_paths_count"]
            return isinstance(count, int) and count >= 1

        assert _wait_until(edit_and_observe)
        assert monitor.awareness_status["mode"] != "live_fallback"
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)
