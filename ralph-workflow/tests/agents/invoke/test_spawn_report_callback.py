from __future__ import annotations

import io
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import PostExitVerdict, TimeoutPolicy
from ralph.agents.invoke import (
    AgentRunCtx,
    InvokeOptions,
    run_pty_and_read_lines,
    run_subprocess_and_read_lines,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer


class _FakeProcessTeardown:
    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
    ) -> None:
        del host_pid, issuer, owner_label


class _FakeSpawnedHandle:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.stdin = None
        self.stdout = io.StringIO("done\n")
        self.stderr = io.StringIO("")
        self.returncode = 0

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode

    def terminate(self, **_kwargs: object) -> None:
        pass

    def kill(self, **_kwargs: object) -> None:
        pass

    def descendant_snapshot(self) -> tuple[int, float]:
        return (0, 0.0)

    def __enter__(self) -> _FakeSpawnedHandle:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _fake_process_manager(handle: _FakeSpawnedHandle) -> SimpleNamespace:
    return SimpleNamespace(
        spawn=lambda *args, **kwargs: handle,
        spawn_pty=lambda *args, **kwargs: handle,
        register_listener=lambda callback: lambda: None,
    )


def _fake_pty_line_reader(*args: object, **kwargs: object) -> object:
    del args, kwargs
    return SimpleNamespace(
        completion_exit_sent=False,
        read_lines=lambda: iter(()),
        discover_captured_session_id=lambda: None,
    )


def _fake_post_exit_watchdog(policy: object, clock: object) -> object:
    del policy, clock
    return SimpleNamespace(wait_for_process_exit=lambda is_exited_fn: PostExitVerdict.CONTINUE)


def _agent_run_ctx(tmp_path: Path, transport: AgentTransport) -> AgentRunCtx:
    return AgentRunCtx(
        config=AgentConfig(cmd="fake-agent", transport=transport),
        show_progress=False,
        extra_env={},
        workspace_path=tmp_path,
        policy=TimeoutPolicy(
            idle_timeout_seconds=60.0,
            drain_window_seconds=0.0,
            process_monitor_enabled=False,
        ),
        process_teardown=_FakeProcessTeardown(),
        requires_completion_evidence=False,
    )


def test_subprocess_spawn_reports_the_spawned_pid_to_the_installed_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reported: list[int] = []
    handle = _FakeSpawnedHandle(pid=4321)
    monkeypatch.setattr(
        "ralph.agents.invoke._process_reader.get_process_manager",
        lambda: _fake_process_manager(handle),
    )
    ctx = _agent_run_ctx(tmp_path, AgentTransport.GENERIC)

    with InvokeOptions.spawn_report_override(reported.append):
        list(run_subprocess_and_read_lines(["fake-agent"], ctx))

    assert reported == [4321]


def test_pty_spawn_reports_the_spawned_pid_to_the_installed_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reported: list[int] = []
    handle = _FakeSpawnedHandle(pid=4242)
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.get_process_manager",
        lambda: _fake_process_manager(handle),
    )
    monkeypatch.setattr("ralph.agents.invoke._pty_runner.PtyLineReader", _fake_pty_line_reader)
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.PostExitWatchdog", _fake_post_exit_watchdog
    )
    ctx = _agent_run_ctx(tmp_path, AgentTransport.CLAUDE_INTERACTIVE)

    with InvokeOptions.spawn_report_override(reported.append):
        list(run_pty_and_read_lines(["fake-agent", "--print"], ctx))

    assert reported == [4242]


def test_a_raising_spawn_report_callback_does_not_break_the_spawn_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    handle = _FakeSpawnedHandle(pid=4321)
    monkeypatch.setattr(
        "ralph.agents.invoke._process_reader.get_process_manager",
        lambda: _fake_process_manager(handle),
    )
    ctx = _agent_run_ctx(tmp_path, AgentTransport.GENERIC)
    attempted: list[int] = []

    def _raising_callback(pid: int) -> None:
        attempted.append(pid)
        raise RuntimeError(f"spawn report failed for {pid}")

    with InvokeOptions.spawn_report_override(_raising_callback):
        list(run_subprocess_and_read_lines(["fake-agent"], ctx))

    assert attempted == [4321]
