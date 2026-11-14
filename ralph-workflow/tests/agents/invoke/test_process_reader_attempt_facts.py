from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    AgentRunCtx,
    ProcessLineReader,
    ProcessReaderCtx,
    run_subprocess_and_read_lines,
)
from ralph.agents.invoke.opencode_subagent_sessions import OpenCodeChildPart
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.liveness import FakeLivenessProbe
from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessTerminationIssuer,
    install_process_manager,
    reset_process_manager,
)
from ralph.testing.fake_process import make_sync_process_factory

if TYPE_CHECKING:
    from pathlib import Path

_PARENT_LINES = (
    '{"type":"step_start","sessionID":"ses_parent","part":{"type":"step-start"}}\n',
    '{"type":"tool_use","sessionID":"ses_parent","part":{"type":"tool","tool":"read",'
    '"state":{"status":"completed","input":{"filePath":"README.md"}}}}\n',
    '{"type":"text","sessionID":"ses_parent","part":{"type":"text","text":"done"}}\n',
)


class _ClockGatedChildSource:
    def __init__(self, clock: FakeClock, active_from: float) -> None:
        self._clock = clock
        self._active_from = active_from
        self._n = 0

    def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
        del parent_session_id, since_ms
        now = self._clock.monotonic()
        if now < self._active_from:
            return []
        self._n += 1
        return [
            OpenCodeChildPart(
                "ses_child",
                "Sisyphus-Junior",
                "Fix gate",
                f"prt_{self._n}",
                "tool:bash",
                int(now * 1000),
            )
        ]

    def close(self) -> None:
        return


class _ExitedHandle:
    stdin = None

    def __init__(self) -> None:
        self.stdout = iter(_PARENT_LINES)
        self.stderr = iter(())
        self.pid = None
        self.returncode: int | None = 0

    def terminate(self, grace_period_s: float | None = None, **_kwargs: object) -> None:
        del grace_period_s

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


class _RecordingProcessTeardown:
    def __init__(self) -> None:
        self.calls: list[tuple[int, ProcessTerminationIssuer]] = []

    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
    ) -> None:
        del owner_label
        self.calls.append((host_pid, issuer))


def _reader(tmp_path: Path, *, child_active_from: float) -> ProcessLineReader:
    clock = FakeClock(start=0.0)
    return ProcessLineReader(
        _ExitedHandle(),
        ProcessReaderCtx(
            config=AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE),
            policy=TimeoutPolicy(
                idle_timeout_seconds=300.0,
                drain_window_seconds=10.0,
                idle_poll_interval_seconds=1.0,
                max_session_seconds=None,
            ),
            execution_strategy=OpenCodeExecutionStrategy(label_scope=None, registry=None),
            liveness_probe=FakeLivenessProbe(active=False),
            opencode_child_part_source=_ClockGatedChildSource(clock, child_active_from),
            workspace_path=tmp_path,
        ),
        clock,
    )


def test_subagent_activity_during_the_drain_is_recorded_on_a_normal_exit(tmp_path: Path) -> None:
    reader = _reader(tmp_path, child_active_from=3.0)

    list(reader.read_lines())

    facts = reader.termination_recorder.facts()
    assert facts.subagent_activity_observed is True
    assert facts.elapsed_to_terminal_ms is not None


def test_quiet_normal_exit_records_no_subagent_activity(tmp_path: Path) -> None:
    reader = _reader(tmp_path, child_active_from=10_000.0)

    list(reader.read_lines())

    facts = reader.termination_recorder.facts()
    assert facts.subagent_activity_observed is not True
    assert facts.elapsed_to_terminal_ms is not None


def test_event_kinds_and_completed_tool_calls_are_recorded_in_order(tmp_path: Path) -> None:
    reader = _reader(tmp_path, child_active_from=10_000.0)

    list(reader.read_lines())

    facts = reader.termination_recorder.facts()
    assert facts.tool_calls == ("read",)
    assert "tool_use" in facts.event_kinds
    assert facts.event_kinds.index("tool_use") < len(facts.event_kinds) - 1
    assert facts.workspace_changed_by_tool is None


def test_startup_failure_missing_stdout_single_teardown() -> None:
    recording = _RecordingProcessTeardown()
    install_process_manager(
        ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.0,
                kill_followup_timeout_s=0.0,
                log_events=False,
                enable_zombie_reaper=False,
            ),
            sync_process_factory=make_sync_process_factory(iter((4242,)), returncode=None),
            psutil=None,
        )
    )
    ctx = AgentRunCtx(
        config=AgentConfig(cmd="/bin/sh", transport=AgentTransport.GENERIC),
        show_progress=False,
        extra_env=None,
        workspace_path=None,
        policy=TimeoutPolicy(idle_timeout_seconds=5.0),
        process_teardown=recording,
    )

    try:
        with pytest.raises(AgentInvocationError):
            list(run_subprocess_and_read_lines(["/bin/sh"], ctx))
    finally:
        reset_process_manager()

    assert recording.calls == [(4242, ProcessTerminationIssuer.INVOKE_TEARDOWN)]
