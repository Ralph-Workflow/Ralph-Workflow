"""The workspace monitor may not stand between Ralph and an agent launch.

``invoke_agent`` starts a :class:`WorkspaceMonitor` AFTER it logs
``Invoking agent: <argv>`` and BEFORE it spawns the agent process. Every
step of that start is optional -- the monitor is an activity signal for
the idle watchdog, nothing the agent needs -- but on the way in it reads
the host's live inotify budget by sweeping ``/proc/<pid>/fdinfo`` and
counts the workspace's directories with ``os.walk``, neither of which
has a time bound. A hung network mount inside the workspace, or one
process in uninterruptible sleep, therefore parks the run between those
two lines: the operator sees the argv, no agent process is ever created,
no file is ever written, and nothing times out, because every watchdog
Ralph owns lives on the far side of a spawn that never happened.

The rule these tests pin is that the probe fails OPEN: when it cannot
answer quickly, the monitor gives up on watching and the launch
proceeds.
"""

from __future__ import annotations

import threading
from pathlib import Path

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness

#: The probe budget these tests give the monitor. Production's default
#: is far larger; what is under test is that SOME bound exists and that
#: the monitor honours it, not the value.
_PROBE_BUDGET_SECONDS = 0.05

#: How long the hung probe answers in. Far beyond the suite's per-test
#: ceiling, which is what fails a regression here: an unbounded start
#: does not return slowly, it does not return, and the test times out
#: exactly as the run does.
_HUNG_PROBE_SECONDS = 20.0


def _hung_counter(workspace: Path, cap: int) -> int | None:
    """A directory count that never answers, like a hung mount's walk."""
    del workspace, cap
    threading.Event().wait(timeout=_HUNG_PROBE_SECONDS)
    return 0


def test_a_capacity_probe_that_never_answers_does_not_park_the_start(
    tmp_path: Path,
) -> None:
    """A wedged capacity probe costs the WATCH, never the launch."""
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=_hung_counter,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
        # Reaching this line at all is half the assertion: an unbounded
        # probe never gets here, and the agent is never spawned.
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert status["mode"] == "live_fallback"


def test_a_probe_that_answers_is_still_believed(tmp_path: Path) -> None:
    """The time bound must not cost the monitor its real answer."""
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert status["mode"] != "live_fallback"
