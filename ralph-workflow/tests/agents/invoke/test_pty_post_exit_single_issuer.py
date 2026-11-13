from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import PostExitVerdict
from ralph.agents.idle_watchdog._timeout_profile import TimeoutProfile
from ralph.agents.invoke import AgentInactivityTimeoutError, AttemptEnded
from ralph.agents.invoke._pty_runner import run_pty_and_read_lines
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    CompletionObserved,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.manager import ProcessTerminationIssuer
from ralph.recovery.attempt_facts import TerminationIssuer

if TYPE_CHECKING:
    from pathlib import Path

    import pytest as pytest_mod
else:
    pytest_mod = pytest


class _PostExitHangHandle:
    def __init__(self) -> None:
        self.pid = 4242
        self.terminate_issuers: list[ProcessTerminationIssuer] = []

    def __enter__(self) -> _PostExitHangHandle:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    def poll(self) -> None:
        return None

    def terminate(
        self,
        grace_period_s: float = 0.5,
        *,
        issuer: ProcessTerminationIssuer,
        reason: str = "",
    ) -> None:
        del grace_period_s, reason
        self.terminate_issuers.append(issuer)


def _fake_pty_line_reader(*args: object, **kwargs: object) -> object:
    del args, kwargs
    return SimpleNamespace(
        completion_exit_sent=False,
        read_lines=lambda: iter(()),
        discover_captured_session_id=lambda: None,
    )


def _fake_post_exit_watchdog(policy: object, clock: object) -> object:
    del policy, clock
    return SimpleNamespace(
        wait_for_process_exit=lambda is_exited_fn: PostExitVerdict.FIRE_PROCESS_EXIT_HANG
    )


def test_pty_post_exit_hang_records_watchdog_issuer_exactly_once(
    monkeypatch: pytest_mod.MonkeyPatch,
    tmp_path: Path,
) -> None:
    handle = _PostExitHangHandle()
    teardown_calls: list[tuple[int, ProcessTerminationIssuer]] = []

    def _record_teardown(pid: int, *, issuer: ProcessTerminationIssuer) -> None:
        teardown_calls.append((pid, issuer))

    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.get_process_manager",
        lambda: SimpleNamespace(spawn_pty=lambda *args, **kwargs: handle),
    )
    monkeypatch.setattr("ralph.agents.invoke._pty_runner.PtyLineReader", _fake_pty_line_reader)
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.PostExitWatchdog", _fake_post_exit_watchdog
    )
    monkeypatch.setattr("ralph.agents.invoke._pty_runner.teardown_subtree", _record_teardown)

    ctx = SimpleNamespace(
        clock=FakeClock(),
        workspace_path=tmp_path,
        extra_env={},
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        show_progress=False,
        policy=SimpleNamespace(
            process_exit_wait_seconds=0.1,
            profile=TimeoutProfile.STANDARD,
        ),
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
        monitor=None,
        required_artifact=None,
        requires_completion_evidence=True,
        evaluate_completion_fn=lambda *args, **kwargs: None,
    )

    with pytest.raises(AgentInactivityTimeoutError) as caught:
        list(run_pty_and_read_lines(["claude", "--print"], ctx))

    assert isinstance(caught.value, AttemptEnded)
    facts = caught.value.facts
    assert facts.termination is not None
    assert [entry.issuer for entry in facts.termination.log] == [
        TerminationIssuer.POST_EXIT_WATCHDOG
    ]
    assert facts.termination.liveness_observed is True
    assert handle.terminate_issuers == [ProcessTerminationIssuer.POST_EXIT_WATCHDOG]
    assert teardown_calls == [(4242, ProcessTerminationIssuer.POST_EXIT_WATCHDOG)]


def test_pty_post_exit_hang_after_prior_winner_publishes_nothing(
    monkeypatch: pytest_mod.MonkeyPatch,
    tmp_path: Path,
) -> None:
    handle = _PostExitHangHandle()
    teardown_calls: list[tuple[int, ProcessTerminationIssuer]] = []
    supervisors: list[AttemptSupervisor] = []

    def _record_teardown(pid: int, *, issuer: ProcessTerminationIssuer) -> None:
        teardown_calls.append((pid, issuer))

    def _supervisor_with_prior_winner(handle_obj: object) -> AttemptSupervisor:
        supervisor = AttemptSupervisor(handle_obj)
        supervisors.append(supervisor)
        outcome = supervisor.act(CompletionObserved(), teardown=_record_teardown)
        assert outcome is not None
        return supervisor

    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.get_process_manager",
        lambda: SimpleNamespace(spawn_pty=lambda *args, **kwargs: handle),
    )
    monkeypatch.setattr("ralph.agents.invoke._pty_runner.PtyLineReader", _fake_pty_line_reader)
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.PostExitWatchdog", _fake_post_exit_watchdog
    )
    monkeypatch.setattr("ralph.agents.invoke._pty_runner.teardown_subtree", _record_teardown)
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.AttemptSupervisor", _supervisor_with_prior_winner
    )

    ctx = SimpleNamespace(
        clock=FakeClock(),
        workspace_path=tmp_path,
        extra_env={},
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        show_progress=False,
        policy=SimpleNamespace(
            process_exit_wait_seconds=0.1,
            profile=TimeoutProfile.STANDARD,
        ),
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
        monitor=None,
        required_artifact=None,
        requires_completion_evidence=True,
        evaluate_completion_fn=lambda *args, **kwargs: None,
    )

    assert list(run_pty_and_read_lines(["claude", "--print"], ctx)) == []

    (supervisor,) = supervisors
    facts = supervisor.recorder.termination()
    assert facts.issuer is TerminationIssuer.AGENT_EXIT
    assert [entry.issuer for entry in facts.log] == [TerminationIssuer.AGENT_EXIT]
    assert [record.acted for record in supervisor.observations] == [True, False]
    assert handle.terminate_issuers == [ProcessTerminationIssuer.INVOKE_TEARDOWN]
    assert teardown_calls == [(4242, ProcessTerminationIssuer.INVOKE_TEARDOWN)]
