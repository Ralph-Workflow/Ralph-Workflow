"""E5.4: the MCP bridge treats an exited-but-unreaped (zombie) child as DEAD.

Catalogue id: **E5.4** (processes & signals — child-death classification).

What this file replaces
-----------------------
``tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e54_zombie_child_reaped_by_poll``
pinned a raw ``subprocess.Popen.poll()`` OS property: it proved that
``os.kill(pid, 0)`` succeeds on a zombie and that ``poll()`` reaps it. It never
constructed a ``RestartAwareMcpBridge``, so a regression of the bridge's own
liveness test back to a bare-PID probe (``os.kill(pid, 0)``) would leave that
test green. This file pins the *bridge boundary*: the observable outcome of
``RestartAwareMcpBridge.check_health_and_restart_if_needed()`` when the serving
child is a zombie.

Why a deterministic process double instead of a real spawned zombie child
----------------------------------------------------------------------
A real zombie child **is** constructible on this platform (spawn a
short-lived ``sys.executable``, do not reap it, observe
``psutil.STATUS_ZOMBIE``, and ``os.kill(pid, 0)`` still succeeds — verified on
this host). A real-child version of this test was rejected for two concrete,
verifiable reasons — either one leaves the pin un-runnable in the default gate
or in violation of the test-policy audit:

1. **It would not run in the default gate.** ``ralph/test_suites.py`` excludes
   any module carrying a module-level ``pytestmark = pytest.mark.subprocess_e2e``
   from ``discover_test_files()`` (``_inspect_test_file`` returns ``include=False``
   unless the file is listed in
   ``ralph.test_suites.REQUIRED_AUTO_INTEGRATE_E2E_FILES``). ``make test``
   (``uv run python -m ralph.test_suites``) is the only broad test step in
   ``ralph/verify.py:_VERIFY_STEPS``; the broad ``subprocess_e2e`` profile is
   excluded from it. Adding the registry entry requires editing
   ``ralph/test_suites.py`` — production source outside this test's write scope
   — and both parity tests (``tests/test_test_suites.py`` and
   ``tests/test_test_suites_orchestration.py``) pin that registry against a
   hard-coded literal, so the entry cannot be added piecemeal either. An
   un-registered real-child file is collected by **zero** ``make verify`` steps,
   which is precisely the silent rot ``AGENTS.md`` forbids.

2. **The test-policy audit would reject it.** ``ralph/testing/audit_test_policy.py``
   grants a whole-file exemption *only* to modules whose AST carries a real
   ``pytest.mark.subprocess_e2e`` marker (``_collect_subprocess_e2e_files`` ->
   ``audit_test_file`` early-return). A module that spawns a real child *without*
   that marker is flagged by construction: the poll loop needs ``time.sleep(...)``
   (a ``sleep`` violation) and a deadline needs ``time.monotonic()`` (a
   ``wall-clock`` violation), and both are flagged regardless of the
   ``subprocess_e2e`` string check. Shaping the test to dodge those detectors
   would be circumventing the audit, not complying with it.

The double therefore reproduces the two *signals that actually disagree* for a
zombie, against a **real, live OS pid**:

* ``poll()`` returns a non-``None`` exit status — the child has exited and the
  handle carries a terminal status (what ``poll()`` reports once it reaps a
  zombie).
* the bare-PID liveness probe ``os.kill(pid, 0)`` **succeeds** on that same pid
  — the pid is still in the OS process table, so a PID-only liveness test would
  wrongly call it alive. This half is exercised against the real OS, not faked:
  the pid used is the live pid of the running test process.

Those two facts are the whole of the zombie ambiguity. Asserting the bridge's
decision while both hold is a strictly stronger pin than the ``Popen.poll()``
property it replaces, and unlike the real-child version it stays on the default
gate.

Follow-up owed by the owning session (out of this file's write scope)
--------------------------------------------------------------------
A real-child companion remains worth adding: spawn a real child, leave it
unreaped, and assert the same bridge outcome. It requires the
``subprocess_e2e`` marker **plus** a matching entry in
``ralph.test_suites.REQUIRED_AUTO_INTEGRATE_E2E_FILES`` and its two hard-coded
parity literals, all of which live in production/test-registry files this
worker may not touch.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import psutil

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_health_event import (
    RestartDecision,
    RestartReason,
    get_restart_decisions,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

#: Exit status reported by the zombie double. Any non-``None`` value is a
#: terminal status; a distinctive value keeps the assertion from passing by
#: coincidence if the status is ever propagated into the recorded decision.
_ZOMBIE_EXIT_STATUS: int = 17

#: Endpoint the bridge publishes. Production keeps it stable across restarts,
#: so it is the same for the zombie child and its replacement.
_ENDPOINT = "http://127.0.0.1:9100/mcp"

#: Synthetic pids for replacement children. The bridge only ever reads them off
#: the child handle, never off the pid table, on the cold-replacement path.
_REPLACEMENT_PID: int = 900_001
_SECOND_REPLACEMENT_PID: int = 900_002


def _open_fd_count() -> int:
    """Return the number of file descriptors open in this process."""
    try:
        return int(psutil.Process().num_fds())
    except (AttributeError, OSError, psutil.Error):
        pass
    fd_dir = Path("/proc/self/fd")
    try:
        return sum(1 for _ in fd_dir.iterdir()) - 1
    except OSError:
        return 0


def _child_pids() -> frozenset[int]:
    """Return the pids of this process's direct children."""
    try:
        return frozenset(child.pid for child in psutil.Process().children())
    except (AttributeError, OSError, psutil.Error):
        return frozenset()


def _bare_pid_probe_says_alive(pid: int) -> bool:
    """Return whether a bare-PID liveness probe considers ``pid`` alive.

    This is exactly the ``os.kill(pid, 0)`` probe: it succeeds for a live
    process AND for an exited-but-unreaped zombie, which is why it cannot be
    the bridge's liveness test.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class _ZombieChild:
    """An exited child whose pid is still present in the OS process table.

    ``poll()`` reports a terminal exit status while the bare-PID liveness probe
    on the same ``pid`` succeeds — the defining zombie ambiguity. Every call the
    bridge makes on this handle is recorded so the test can assert which
    shutdown signals the bridge considered necessary.
    """

    def __init__(self, pid: int, exit_status: int) -> None:
        self._pid = pid
        self._exit_status = exit_status
        self.poll_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        self.poll_calls += 1
        return self._exit_status

    def wait(self, timeout: float | None = None) -> int:
        return self._exit_status

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1

    def cleanup_orphans(self) -> None:
        return


class _LiveChild:
    """A still-running child: ``poll()`` is ``None`` until it is terminated."""

    def __init__(self, pid: int) -> None:
        self._pid = pid
        self._exit_status: int | None = None
        self.poll_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        self.poll_calls += 1
        return self._exit_status

    def wait(self, timeout: float | None = None) -> int | None:
        return self._exit_status

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self.terminate_calls += 1
        self._exit_status = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self._exit_status = -9

    def cleanup_orphans(self) -> None:
        return


class _ReplacementSource:
    """The bridge's ``restart_fn``; records how the replacement was requested.

    A cold replacement calls ``restart_fn()``; the make-before-break path calls
    ``restart_fn(make_before_break=True)`` because a candidate must overlap a
    *serving* old child. A zombie cannot serve, so its replacement must be
    requested without the overlap.
    """

    def __init__(self, replacement: StandaloneMcpProcess) -> None:
        self._replacement = replacement
        self.calls = 0
        self.make_before_break_requests = 0

    def __call__(self, *, make_before_break: bool = False) -> StandaloneMcpProcess:
        self.calls += 1
        if make_before_break:
            self.make_before_break_requests += 1
        return self._replacement


def _make_inner(
    process: _ZombieChild | _LiveChild,
    session_file: Path,
) -> StandaloneMcpProcess:
    return StandaloneMcpProcess(
        endpoint=_ENDPOINT,
        process=process,
        session_file=session_file,
    )


def _make_bridge(
    inner: StandaloneMcpProcess,
    replacement: StandaloneMcpProcess,
) -> tuple[lifecycle.RestartAwareMcpBridge, _ReplacementSource]:
    source = _ReplacementSource(replacement)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=source,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="e54-zombie-run",
        sleep_fn=lambda _seconds: None,
    )
    return bridge, source


def test_e54_zombie_child_is_treated_as_dead_and_replaced_by_bridge(tmp_path: Path) -> None:
    """E5.4: a zombie serving child is classified dead and replaced immediately.

    The pin: the bridge restarts on the FIRST health check, with zero prior
    probe failures, while a bare-PID liveness probe on the same pid still says
    "alive". A PID-only liveness regression would fall through to the probe
    path, return ``False`` and replace nothing.
    """
    fds_before = _open_fd_count()
    threads_before = threading.active_count()
    children_before = _child_pids()

    live_pid = os.getpid()
    assert _bare_pid_probe_says_alive(live_pid), (
        "precondition: the bare-PID liveness probe must succeed on this pid, "
        "otherwise the zombie ambiguity this test pins is not reproduced"
    )

    zombie = _ZombieChild(pid=live_pid, exit_status=_ZOMBIE_EXIT_STATUS)
    inner = _make_inner(zombie, tmp_path / "zombie-session.json")
    replacement = _make_inner(
        _LiveChild(pid=_REPLACEMENT_PID),
        tmp_path / "replacement-session.json",
    )
    bridge, source = _make_bridge(inner, replacement)

    # The decision log is a bounded ring buffer, so its length stops growing once
    # earlier tests fill it; identify a new record by its newest entry instead.
    newest_before = get_restart_decisions()[-1:]
    try:
        restarted = bridge.check_health_and_restart_if_needed()

        assert restarted is True, "a zombie child must be replaced, not tolerated"
        assert bridge.restart_count == 1
        assert bridge.process.pid == _REPLACEMENT_PID
        assert bridge.endpoint_uri() == _ENDPOINT

        # The zombie was already dead: the bridge reaps it via poll/wait and
        # never signals it.
        assert zombie.poll_calls > 0
        assert zombie.terminate_calls == 0
        assert zombie.kill_calls == 0

        # A zombie cannot serve, so the replacement must be cold — no
        # make-before-break overlap was attempted.
        assert source.calls == 1
        assert source.make_before_break_requests == 0

        recorded: tuple[RestartDecision, ...] = get_restart_decisions()
        assert recorded, "the restart must be recorded"
        assert not newest_before or recorded[-1] is not newest_before[0], (
            "the restart must be recorded"
        )
        decision = recorded[-1]
        assert decision.reason == RestartReason.PROCESS_EXITED
        assert decision.stage == "process_exited"
        assert decision.exit_status == _ZOMBIE_EXIT_STATUS
        assert decision.probe_duration_s is None
    finally:
        bridge.shutdown()

    assert _child_pids() == children_before, "test leaked a child process"
    assert threading.active_count() <= threads_before, "test leaked a thread"
    assert _open_fd_count() <= fds_before, "test leaked a file descriptor"


def test_e54_bridge_separates_zombie_from_live_child_on_the_same_alive_pid(
    tmp_path: Path,
) -> None:
    """E5.4: identical bare-PID liveness, opposite bridge decisions.

    Both children sit on a pid for which ``os.kill(pid, 0)`` succeeds — the only
    signal a PID-only liveness test has. The zombie is replaced; the running
    child keeps serving. That the two outcomes differ proves the bridge reads
    the child *handle*, not the pid table.
    """
    fds_before = _open_fd_count()
    threads_before = threading.active_count()
    children_before = _child_pids()

    live_pid = os.getpid()
    assert _bare_pid_probe_says_alive(live_pid)

    zombie_bridge, _zombie_source = _make_bridge(
        _make_inner(
            _ZombieChild(pid=live_pid, exit_status=_ZOMBIE_EXIT_STATUS),
            tmp_path / "zombie-session.json",
        ),
        _make_inner(
            _LiveChild(pid=_REPLACEMENT_PID),
            tmp_path / "zombie-replacement.json",
        ),
    )
    serving = _LiveChild(pid=live_pid)
    live_bridge, live_source = _make_bridge(
        _make_inner(serving, tmp_path / "live-session.json"),
        _make_inner(
            _LiveChild(pid=_SECOND_REPLACEMENT_PID),
            tmp_path / "live-replacement.json",
        ),
    )

    try:
        assert zombie_bridge.check_health_and_restart_if_needed() is True
        assert zombie_bridge.restart_count == 1

        assert live_bridge.check_health_and_restart_if_needed() is False
        assert live_bridge.restart_count == 0
        assert live_bridge.process.pid == live_pid
        assert live_source.calls == 0, "a healthy child must not be replaced"
        assert serving.terminate_calls == 0
        assert serving.kill_calls == 0
    finally:
        zombie_bridge.shutdown()
        live_bridge.shutdown()

    assert _child_pids() == children_before, "test leaked a child process"
    assert threading.active_count() <= threads_before, "test leaked a thread"
    assert _open_fd_count() <= fds_before, "test leaked a file descriptor"
