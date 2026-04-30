"""Black-box tests for ralph.process.ProcessManager.

All tests use real subprocesses; no mocking of subprocess internals.
Grace periods are kept short via ProcessManagerPolicy to stay well within
the 30s suite budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import pathlib
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

import psutil
import pytest
from loguru import logger

from ralph.process import (
    ProcessEvent,
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    get_process_manager,
    process_phase_scope,
    reset_process_manager,
)
from ralph.process.manager import ProcessTerminationError

_FAST_POLICY = ProcessManagerPolicy(default_grace_period_s=0.3, kill_followup_timeout_s=0.5)

PYTHON = sys.executable


@pytest.fixture(autouse=True)
def _reset_pm():
    """Ensure each test starts and ends with a clean singleton."""
    reset_process_manager()
    yield
    with contextlib.suppress(Exception):
        get_process_manager().shutdown_all(grace_period_s=0)
    reset_process_manager()


def _make_pm() -> ProcessManager:
    return ProcessManager(policy=_FAST_POLICY)


# ---------------------------------------------------------------------------
# 1. spawn tracks a Python sleep subprocess: SPAWNED→EXITED with cause='exited'
# ---------------------------------------------------------------------------


def test_spawn_tracks_lifecycle_to_exited() -> None:
    pm = _make_pm()
    events: list[ProcessEvent] = []
    pm.register_listener(events.append)

    handle = pm.spawn([PYTHON, "-c", "import time; time.sleep(0.05)"])
    assert handle.record.status == ProcessStatus.RUNNING
    assert handle.record.pid > 0

    handle.wait()

    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.cause == "exited"
    assert handle.record.returncode == 0
    statuses = [e.new_status for e in events]
    assert ProcessStatus.RUNNING in statuses
    assert ProcessStatus.EXITED in statuses


# ---------------------------------------------------------------------------
# 2. terminate() against a child that exits cleanly on SIGTERM → KILLED
# ---------------------------------------------------------------------------


def test_terminate_graceful_sigterm() -> None:
    pm = _make_pm()

    handle = pm.spawn([PYTHON, "-c", "import time; time.sleep(30)"])
    assert handle.record.status == ProcessStatus.RUNNING

    handle.terminate(grace_period_s=0.3)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


# ---------------------------------------------------------------------------
# 3. terminate() against SIGTERM-trapping child is escalated to SIGKILL
# ---------------------------------------------------------------------------


def test_terminate_escalates_to_sigkill() -> None:
    pm = _make_pm()

    handle = pm.spawn(
        [
            PYTHON,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ]
    )
    start = time.monotonic()
    handle.terminate(grace_period_s=0.2)
    elapsed = time.monotonic() - start

    assert handle.record.status == ProcessStatus.KILLED
    assert elapsed < 2.0  # noqa: PLR2004


# ---------------------------------------------------------------------------
# 4. spawn_async integration with asyncio streams
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_async_captures_output() -> None:
    pm = _make_pm()
    events: list[ProcessEvent] = []
    pm.register_listener(events.append)

    handle = await pm.spawn_async(
        [PYTHON, "-c", "print('hello async')"],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _stderr = await handle.communicate()

    assert b"hello async" in stdout
    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.cause == "exited"


# ---------------------------------------------------------------------------
# 5. shutdown_all kills three concurrently running children
# ---------------------------------------------------------------------------


def test_shutdown_all_kills_multiple_children() -> None:
    pm = _make_pm()
    handles = [pm.spawn([PYTHON, "-c", "import time; time.sleep(30)"]) for _ in range(3)]
    pids = [h.record.pid for h in handles]

    pm.shutdown_all(grace_period_s=0)

    for h in handles:
        assert h.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)

    for pid in pids:
        assert not psutil.pid_exists(pid), f"PID {pid} still alive after shutdown_all"


# ---------------------------------------------------------------------------
# 6. listener receives exactly one event per transition; unsubscribe() stops events
# ---------------------------------------------------------------------------


def test_listener_receives_one_event_per_transition_and_unsubscribe_works() -> None:
    pm = _make_pm()
    events_a: list[ProcessEvent] = []
    events_b: list[ProcessEvent] = []

    unsub_a = pm.register_listener(events_a.append)
    _unsub_b = pm.register_listener(events_b.append)

    handle = pm.spawn([PYTHON, "-c", "pass"])
    handle.wait()

    unsub_a()

    handle2 = pm.spawn([PYTHON, "-c", "pass"])
    handle2.wait()

    assert len(events_a) == 2, f"expected 2 events for handle1, got {len(events_a)}"  # noqa: PLR2004
    assert len(events_b) == 4, f"expected 4 events for both handles, got {len(events_b)}"  # noqa: PLR2004


# ---------------------------------------------------------------------------
# 7. failure to exec (missing binary) emits FAILED event and raises
# ---------------------------------------------------------------------------


def test_failed_exec_emits_failed_event() -> None:
    pm = _make_pm()
    events: list[ProcessEvent] = []
    pm.register_listener(events.append)

    with pytest.raises(OSError):
        pm.spawn(["definitely-not-a-real-command-xyz-ralph"])

    failed_events = [e for e in events if e.new_status == ProcessStatus.FAILED]
    assert len(failed_events) == 1


# ---------------------------------------------------------------------------
# 8. shutdown_all_for_label kills only prefixed processes
# ---------------------------------------------------------------------------


def test_shutdown_all_for_label_kills_only_matching() -> None:
    pm = _make_pm()

    target = pm.spawn(
        [PYTHON, "-c", "import time; time.sleep(30)"],
        label="worker:target",
    )
    bystander = pm.spawn(
        [PYTHON, "-c", "import time; time.sleep(30)"],
        label="other:bystander",
    )

    pm.shutdown_all_for_label("worker:", grace_period_s=0)

    assert target.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)
    assert bystander.record.status == ProcessStatus.RUNNING

    bystander.terminate(grace_period_s=0)


# ---------------------------------------------------------------------------
# 9. raising listener doesn't break lifecycle progression
# ---------------------------------------------------------------------------


def test_raising_listener_does_not_break_lifecycle() -> None:
    pm = _make_pm()
    good_events: list[ProcessEvent] = []

    def bad_listener(event: ProcessEvent) -> None:
        raise RuntimeError("listener exploded")

    pm.register_listener(bad_listener)
    pm.register_listener(good_events.append)

    handle = pm.spawn([PYTHON, "-c", "pass"])
    handle.wait()

    assert handle.record.status == ProcessStatus.EXITED
    assert len(good_events) >= 1


# ---------------------------------------------------------------------------
# 10. process teardown via psutil (cross-platform, no killpg dependency)
# ---------------------------------------------------------------------------


def test_process_group_is_torn_down_on_terminate() -> None:
    pm = _make_pm()
    handle = pm.spawn(
        [PYTHON, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    pid = handle.record.pid
    assert pid > 0

    handle.terminate(grace_period_s=0.2)

    assert handle.record.status == ProcessStatus.KILLED

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"Process {pid} still alive after terminate")


# ---------------------------------------------------------------------------
# 11. Recursive process-tree teardown: parent + grandchild both die
# ---------------------------------------------------------------------------


@pytest.mark.subprocess_e2e
def test_recursive_process_tree_teardown() -> None:
    """Both the parent and its forked grandchild are gone after shutdown_all_for_label."""
    pm = _make_pm()
    code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "time.sleep(30)"
    )
    handle = pm.spawn([PYTHON, "-c", code], label="tree-kill-test")
    parent_pid = handle.record.pid

    # Give the grandchild a moment to spawn before teardown.
    time.sleep(0.3)

    grandchild_pid: int | None = None
    try:
        root = psutil.Process(parent_pid)
        children = root.children(recursive=True)
        grandchild_pid = children[0].pid if children else None
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    pm.shutdown_all_for_label("tree-kill-test", grace_period_s=0.3)

    assert not psutil.pid_exists(parent_pid), f"Parent {parent_pid} still alive after teardown"
    if grandchild_pid is not None:
        assert not psutil.pid_exists(grandchild_pid), (
            f"Grandchild {grandchild_pid} still alive after teardown"
        )


# ---------------------------------------------------------------------------
# End-to-end smoke test: SIGTERM-trap child is force-killed via public API
# ---------------------------------------------------------------------------


def test_end_to_end_sigterm_trap_child() -> None:
    pm = ProcessManager(
        policy=ProcessManagerPolicy(default_grace_period_s=0.2, kill_followup_timeout_s=0.5)
    )
    handle = pm.spawn(
        [
            PYTHON,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ]
    )
    handle.terminate(grace_period_s=0.2)
    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


# ---------------------------------------------------------------------------
# 12. atexit safety net: grandchild is reaped when the host process exits
# ---------------------------------------------------------------------------


@pytest.mark.subprocess_e2e
def test_atexit_reaps_grandchild() -> None:
    """ProcessManager atexit hook terminates tracked children at interpreter exit."""

    script = (
        "import sys, time; "
        "sys.path.insert(0, sys.argv[1]); "
        "from ralph.process.manager import get_process_manager; "
        "pm = get_process_manager(); "
        "h = pm.spawn([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "open(sys.argv[2], 'w').write(str(h.record.pid)); "
        "sys.exit(0)"
    )

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        pid_file = pathlib.Path(f.name)

    ralph_src = (pathlib.Path(__file__).parent.parent).as_posix()
    result = subprocess.run(
        [PYTHON, "-c", script, ralph_src, str(pid_file)],
        timeout=10,
        check=False,
    )
    assert result.returncode == 0

    grandchild_pid_text = pid_file.read_text(encoding="utf-8").strip()
    pid_file.unlink(missing_ok=True)
    grandchild_pid = int(grandchild_pid_text)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not psutil.pid_exists(grandchild_pid):
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"Grandchild {grandchild_pid} still alive after atexit should have killed it")


# ---------------------------------------------------------------------------
# 13. log_events=False suppresses default loguru listener; =True emits lines
# ---------------------------------------------------------------------------


def test_log_events_false_suppresses_loguru_output() -> None:
    """log_events=False produces no process log lines; True produces them."""
    records: list[str] = []
    sink_id = logger.add(lambda msg: records.append(str(msg)), level="DEBUG", format="{message}")
    try:
        # No log events when disabled
        pm_silent = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.1,
                kill_followup_timeout_s=0.2,
                log_events=False,
            )
        )
        handle = pm_silent.spawn([PYTHON, "-c", "pass"])
        handle.wait()
        pid_str = str(handle.record.pid)
        process_lines_silent = [r for r in records if "process " in r and pid_str in r]
        assert process_lines_silent == [], (
            f"Expected no process log lines with log_events=False, got: {process_lines_silent}"
        )

        records.clear()

        # Log events when enabled
        pm_loud = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.1,
                kill_followup_timeout_s=0.2,
                log_events=True,
            )
        )
        handle2 = pm_loud.spawn([PYTHON, "-c", "pass"])
        handle2.wait()
        pid2_str = str(handle2.record.pid)
        process_lines_loud = [r for r in records if "process " in r and pid2_str in r]
        assert len(process_lines_loud) >= 1, (
            f"Expected process log lines with log_events=True, got none. records={records}"
        )
    finally:
        logger.remove(sink_id)


# ---------------------------------------------------------------------------
# 14. process_phase_scope logs a warning on ProcessTerminationError
# ---------------------------------------------------------------------------


def test_process_phase_scope_warns_on_termination_error() -> None:
    """process_phase_scope emits a loguru warning when cleanup fails, but exits cleanly."""

    def _raise_termination_error(label_prefix: str, *, grace_period_s: float | None = None) -> None:
        raise ProcessTerminationError(pid=99999, pgid=99999)

    pm = get_process_manager()

    warning_messages: list[str] = []
    sink_id = logger.add(
        lambda msg: warning_messages.append(str(msg)) if "WARNING" in str(msg) else None,
        level="WARNING",
        format="{level} {message}",
    )
    try:
        with (
            patch.object(pm, "shutdown_all_for_label", _raise_termination_error),
            process_phase_scope("test-phase"),
        ):
            pass
    finally:
        logger.remove(sink_id)

    assert any("test-phase" in msg for msg in warning_messages), (
        f"Expected warning mentioning 'test-phase', got: {warning_messages}"
    )
