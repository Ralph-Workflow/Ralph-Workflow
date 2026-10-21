"""The ``McpSupervisor.__exit__`` terminal-error ordering and its diagnostics.

``McpSupervisor.__exit__`` re-raises the stored terminal
``BUDGET_EXHAUSTED`` ``McpServerError`` UNCONDITIONALLY, so it replaces
whatever agent-level error the supervised body was already propagating
(``ralph/process/mcp_supervisor.py:202``). These tests pin that ordering
against the REAL supervisor, prove the executor still converts it to the
typed ``AGENT_FAILURE`` path end to end, and pin the next-incident
diagnostics a child that survives SIGTERM *and* SIGKILL must leave.

The per-entry-path matrix lives in
``test_terminal_mcp_error_per_entry_path``; both modules share the fake
seams in ``_terminal_mcp_error_seams``.
"""

from __future__ import annotations

import itertools
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.server._mcp_attempt_result import RestartAttemptResult
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.process.manager._spawn_options import SpawnOptions
from ralph.process.mcp_supervisor import AGENT_PROCESS_LABEL_PREFIX, McpSupervisor
from ralph.testing.fake_process import (
    FakePsutil,
    FakePsutilProcess,
    make_async_process_factory,
    make_sync_process_factory,
)
from ralph.workspace import WorkspaceScope
from tests.pipeline._terminal_mcp_error_seams import (
    _SUPERVISOR_WAIT_TIMEOUT_S,
    _TERMINAL_LAST_STAGE,
    _assert_no_traceback_or_crash,
    _assert_typed_retry_intent,
    _deps_with_terminal_supervisor,
    _log_capture,
    _patch_invoke_agent,
    _policy_bundle,
    _SupervisorBridge,
    _terminal_mcp_error,
    _TerminalBridge,
)


def test_supervisor_exit_replaces_in_flight_agent_error_with_terminal_mcp_error() -> None:
    """The REAL ``McpSupervisor.__exit__`` replaces an in-flight error.

    ``mcp_supervisor.py:202`` raises the stored ``BUDGET_EXHAUSTED`` error
    unconditionally, so an agent-level ``AgentInvocationError`` that is
    already propagating is REPLACED. Driven through the real supervisor with
    a fake bridge whose health check is terminal; the only wait is the
    bounded handshake the interrupt seam signals.
    """
    terminal = threading.Event()

    def _note_interrupt(label: str) -> None:
        del label
        terminal.set()

    supervisor = McpSupervisor(
        _TerminalBridge(),
        check_interval=timedelta(0),
        agent_label_scope="run-a",
        terminate_agents=_note_interrupt,
        _record_psi_pressure=lambda: None,
        _now=lambda: 0.0,
    )
    supervisor.__enter__()
    assert terminal.wait(timeout=_SUPERVISOR_WAIT_TIMEOUT_S), (
        "the supervisor never reached its terminal tick"
    )

    in_flight = AgentInvocationError("agent exited 1", 1)
    with pytest.raises(McpServerError) as raised:
        supervisor.__exit__(type(in_flight), in_flight, in_flight.__traceback__)

    assert raised.value.failure_kind == RestartAttemptResult.BUDGET_EXHAUSTED
    assert raised.value.last_stage == _TERMINAL_LAST_STAGE


def test_in_flight_agent_error_is_replaced_by_terminal_conversion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end: the ``__exit__`` ordering still converts typed.

    The supervised body dies with an ``AgentInvocationError`` while the
    supervisor has already gone terminal. The typed conversion must win, so
    the published outcome is ``AGENT_FAILURE`` with the intent naming
    ``McpServerError`` -- not the agent-level retry path.
    """
    deps, factory = _deps_with_terminal_supervisor(tmp_path)
    _patch_invoke_agent(monkeypatch, factory.agent_dies_in_flight)

    with _log_capture() as recorder:
        event = effect_executor_module.execute_agent_effect(
            InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md"),
            UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0)),
            deps,
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            bridge=_TerminalBridge(),
            policy_bundle=_policy_bundle(tmp_path),
        )

    assert event == PipelineEvent.AGENT_FAILURE
    _assert_no_traceback_or_crash(recorder)
    _assert_typed_retry_intent()

    # The ordering really happened: an AgentInvocationError was in flight
    # when the supervisor went terminal, and the terminal McpServerError is
    # what surfaced (otherwise the intent would name the agent failure).
    assert len(factory.supervisors) == 1
    assert list(factory.interrupted_labels) == [
        f"{AGENT_PROCESS_LABEL_PREFIX}{factory.label_scopes[0]}:"
    ]


def test_terminal_supervisor_interrupt_logs_status_and_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The next incident names the psutil status and the command.

    ``McpSupervisor`` interrupts every agent process under the run label
    once supervision is terminal (``mcp_supervisor.py:169``). An agent that
    survives SIGTERM *and* SIGKILL must leave a next-incident record that
    says which status psutil saw and which command it saw, so an operator
    can identify the wedged child instead of guessing.
    """

    class _SurvivingPsutilProcess(FakePsutilProcess):
        def __init__(self, pid: int) -> None:
            super().__init__(pid=pid, stubborn=True)

        def status(self) -> str:
            return "disk-sleep"

        def cmdline(self) -> list[str]:
            return ["opencode", "run", "--json-stream"]

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    pid = 9001
    fake_psutil = FakePsutil()
    fake_psutil._processes = {pid: _SurvivingPsutilProcess(pid)}
    manager = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        sync_process_factory=make_sync_process_factory(itertools.count(pid)),
        async_process_factory=make_async_process_factory(itertools.count(pid + 1)),
        psutil=fake_psutil,
    )
    label = f"{AGENT_PROCESS_LABEL_PREFIX}run-a:"
    manager.spawn(["opencode", "run", "--json-stream"], SpawnOptions(label=label))

    monkeypatch.setattr("ralph.process.mcp_supervisor.get_process_manager", lambda: manager)

    with _log_capture() as recorder:
        supervisor = McpSupervisor(
            _SupervisorBridge(),
            check_interval=timedelta(seconds=2),
            agent_label_scope="run-a",
        )
        supervisor._interrupt_blocked_invocation(_terminal_mcp_error(child_pid=pid))

    surviving = [
        record for record in recorder.records if "still alive after kill (status=" in record
    ]
    assert surviving, (
        "a child that survives SIGTERM and SIGKILL must leave a next-incident "
        f"record; captured: {recorder.records}"
    )
    assert all("status=disk-sleep" in record for record in surviving), surviving
    assert all("command=opencode run --json-stream" in record for record in surviving), surviving
