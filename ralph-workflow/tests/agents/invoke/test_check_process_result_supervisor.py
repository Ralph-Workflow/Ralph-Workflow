from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    attempt_facts_from_exception,
    check_process_result,
)
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke.attempt_supervision import AttemptSupervisor
from ralph.agents.timeout_clock import FakeClock, SystemClock
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.process.liveness import FakeLivenessProbe
from ralph.process.manager import ManagedProcess, ProcessManager, SpawnOptions
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer

if TYPE_CHECKING:
    from pathlib import Path

_LINGERING_DESCENDANT_CHILD = (
    "import os, sys;"
    "pid = os.fork();"
    "(open({pid_file}, 'w').write(str(pid)), sys.exit(0)) if pid else "
    "os.execv(sys.executable, [sys.executable, '-c', 'import time; time.sleep(30)']);"
)


def _no_signals(
    workspace: Path,
    raw_output: list[str] | None = None,
    *,
    required_artifact: object = None,
    run_id: object = None,
    sentinel_secret: object = None,
    receipt_secret: object = None,
) -> CompletionSignals:
    return CompletionSignals(False, False, ())


def _spawn_exited_child_with_lingering_descendant(
    tmp_path: Path,
) -> tuple[ProcessManager, ManagedProcess, int]:
    manager = ProcessManager()
    pid_file = tmp_path / "grandchild.pid"
    child = manager.spawn(
        [sys.executable, "-c", _LINGERING_DESCENDANT_CHILD.format(pid_file=repr(str(pid_file)))],
        SpawnOptions(label="invoke:completion-supervisor"),
    )
    try:
        try:
            child.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            pytest.fail("child did not exit")
        grandchild_pid = int(pid_file.read_text().strip())
        os.kill(grandchild_pid, 0)
    except BaseException:
        with contextlib.suppress(Exception):
            child.kill(issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN)
        raise
    return manager, child, grandchild_pid


def _assert_reaped(pid: int) -> None:
    clock = SystemClock()
    deadline = clock.monotonic() + 10.0
    backoff = threading.Event()
    while True:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 0)
            if clock.monotonic() >= deadline:
                pytest.fail(f"descendant {pid} was not reaped")
            clock.wait_for_event(backoff, 0.05)
            continue
        return


def _session_continuation_options(tmp_path: Path) -> CompletionCheckOptions:
    return CompletionCheckOptions(
        execution_strategy=OpenCodeExecutionStrategy(),
        workspace_path=tmp_path,
        liveness_probe=FakeLivenessProbe(active=True),
        required_artifact=RequiredArtifact(
            phase="planning",
            artifact_type="plan",
            artifact_path=".agent/artifacts/plan.md",
            markdown_path=None,
            normalizer=None,
        ),
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            descendant_wait_timeout_seconds=2.0,
            descendant_wait_poll_seconds=0.5,
            parent_exit_grace_seconds=1.0,
        ),
        evaluate_completion_fn=_no_signals,
    )


class TestCheckProcessResultSupervisor:
    def test_explicit_supervisor_reaps_descendant_and_records_fire(self, tmp_path: Path) -> None:
        _manager, child, grandchild_pid = _spawn_exited_child_with_lingering_descendant(tmp_path)
        try:
            supervisor = AttemptSupervisor(child)
            with pytest.raises(OpenCodeResumableExitError) as caught:
                check_process_result(
                    child,
                    "opencode",
                    [],
                    _session_continuation_options(tmp_path),
                    _clock=FakeClock(start=0.0),
                    termination_recorder=supervisor.recorder,
                )

            _assert_reaped(grandchild_pid)
            facts = attempt_facts_from_exception(caught.value)
            assert facts is not None
            assert facts.termination is not None
            assert facts.termination == supervisor.recorder.termination()
            assert facts.termination.issuer is TerminationIssuer.POST_EXIT_WATCHDOG
            assert TerminationIssuer.POST_EXIT_WATCHDOG in [
                entry.issuer for entry in supervisor.recorder.termination().log
            ]
        finally:
            with contextlib.suppress(Exception):
                os.kill(grandchild_pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                child.kill(issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN)

    def test_standalone_supervisor_supplies_attached_facts(self, tmp_path: Path) -> None:
        manager = ProcessManager()
        child = manager.spawn(
            [sys.executable, "-c", "import sys; sys.exit(1)"],
            SpawnOptions(label="invoke:completion-supervisor"),
        )
        try:
            try:
                child.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                pytest.fail("child did not exit")

            with pytest.raises(AgentInvocationError) as caught:
                check_process_result(child, "test-agent", [], None, termination_recorder=None)

            facts: AttemptFacts | None = attempt_facts_from_exception(caught.value)
            assert facts is not None
            assert facts.termination is not None
            assert facts.termination.issuer is TerminationIssuer.AGENT_EXIT
            assert facts.termination.observed_returncode == 1
            assert facts.termination.reported_returncode == 1
        finally:
            with contextlib.suppress(Exception):
                child.kill(issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN)
