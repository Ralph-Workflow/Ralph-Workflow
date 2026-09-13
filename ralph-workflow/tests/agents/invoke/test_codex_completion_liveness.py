"""Codex completion liveness regression coverage."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import ralph.agents.invoke._process_reader as process_reader
from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state._factory import strategy_for_transport
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import AgentRunCtx
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools.coordination import handle_declare_complete
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    import pytest

    from ralph.phases.required_artifacts import RequiredArtifact


class _LiveHandle:
    def __init__(self, stdout: Iterator[str], completed: threading.Event) -> None:
        self.pid = 999_999
        self.stdin = None
        self.stdout = stdout
        self.stderr = None
        self.returncode: int | None = None
        self.terminated = False
        self._completed = completed

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True
        self.returncode = 0
        self._completed.set()

    def descendant_snapshot(self) -> tuple[int, float]:
        return 0, 0.0

    def __enter__(self) -> _LiveHandle:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _terminal_signals(
    workspace: Path,
    raw_output: list[str] | None = None,
    *,
    required_artifact: RequiredArtifact | None = None,
    run_id: str | None = None,
    sentinel_secret: str | None = None,
    receipt_secret: str | None = None,
) -> CompletionSignals:
    del workspace, raw_output, required_artifact, run_id, sentinel_secret, receipt_secret
    return CompletionSignals(
        explicit_complete=True,
        required_artifact_present=False,
        artifact_types=(),
        completion_sentinel_present=True,
    )


def test_codex_declare_complete_stops_a_stream_that_never_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    completed = threading.Event()
    completion_checks = 0
    monkeypatch.setenv("RALPH_BROKER_SECRET", "test-parent-only-secret")

    def live_stdout() -> Iterator[str]:
        yield '{"type":"item.completed"}\n'
        assert completed.wait(timeout=0.1)

    handle = _LiveHandle(live_stdout(), completed)

    class _ProcessManager:
        def spawn(self, _argv: object, _options: object) -> _LiveHandle:
            return handle

        def register_listener(self, _listener: object) -> Callable[[], None]:
            return lambda: None

    monkeypatch.setattr(process_reader, "get_process_manager", _ProcessManager)

    def evaluate(*_args: object, **_kwargs: object) -> CompletionSignals:
        nonlocal completion_checks
        completion_checks += 1
        return CompletionSignals(
            explicit_complete=completion_checks > 1,
            required_artifact_present=False,
            artifact_types=(),
            completion_sentinel_present=completion_checks > 1,
        )

    ctx = AgentRunCtx(
        config=AgentConfig(cmd="codex", transport=AgentTransport.CODEX),
        show_progress=False,
        extra_env={"RALPH_MCP_RUN_ID": "run-1"},
        workspace_path=tmp_path,
        policy=TimeoutPolicy(
            idle_timeout_seconds=5.0,
            max_session_seconds=5.0,
            idle_poll_interval_seconds=0.01,
            drain_window_seconds=0.0,
        ),
        execution_strategy=strategy_for_transport(AgentTransport.CODEX),
        evaluate_completion_fn=evaluate,
    )

    lines = list(process_reader._run_subprocess_and_read_lines(["codex"], ctx))

    assert lines == ['{"type":"item.completed"}\n']
    assert handle.terminated is True


def test_codex_declare_complete_stops_a_stream_that_never_becomes_quiet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    completed = threading.Event()
    monkeypatch.setenv("RALPH_BROKER_SECRET", "test-parent-only-secret")

    def live_stdout() -> Iterator[str]:
        while not completed.is_set():
            yield '{"type":"item.completed"}\n'

    handle = _LiveHandle(live_stdout(), completed)

    class _ProcessManager:
        def spawn(self, _argv: object, _options: object) -> _LiveHandle:
            return handle

        def register_listener(self, _listener: object) -> Callable[[], None]:
            return lambda: None

    monkeypatch.setattr(process_reader, "get_process_manager", _ProcessManager)
    ctx = AgentRunCtx(
        config=AgentConfig(cmd="codex", transport=AgentTransport.CODEX),
        show_progress=False,
        extra_env={"RALPH_MCP_RUN_ID": "run-1"},
        workspace_path=tmp_path,
        policy=TimeoutPolicy(
            idle_timeout_seconds=5.0,
            max_session_seconds=5.0,
            idle_poll_interval_seconds=0.01,
            drain_window_seconds=0.0,
        ),
        execution_strategy=strategy_for_transport(AgentTransport.CODEX),
        evaluate_completion_fn=_terminal_signals,
    )

    lines = list(process_reader._run_subprocess_and_read_lines(["codex"], ctx))

    assert lines
    assert handle.terminated is True


def test_codex_ignores_unsigned_completion_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    completed = threading.Event()
    evaluator_called = False
    monkeypatch.delenv("RALPH_BROKER_SECRET", raising=False)

    def closed_stdout() -> Iterator[str]:
        yield '{"type":"item.completed"}\n'
        handle.returncode = 0

    handle = _LiveHandle(closed_stdout(), completed)

    class _ProcessManager:
        def spawn(self, _argv: object, _options: object) -> _LiveHandle:
            return handle

        def register_listener(self, _listener: object) -> Callable[[], None]:
            return lambda: None

    def evaluate(*_args: object, **_kwargs: object) -> CompletionSignals:
        nonlocal evaluator_called
        evaluator_called = True
        return CompletionSignals(
            explicit_complete=True,
            required_artifact_present=False,
            artifact_types=(),
            completion_sentinel_present=True,
        )

    monkeypatch.setattr(process_reader, "get_process_manager", _ProcessManager)
    ctx = AgentRunCtx(
        config=AgentConfig(cmd="codex", transport=AgentTransport.CODEX),
        show_progress=False,
        extra_env={"RALPH_MCP_RUN_ID": "run-1"},
        workspace_path=tmp_path,
        policy=TimeoutPolicy(
            idle_timeout_seconds=5.0,
            max_session_seconds=5.0,
            idle_poll_interval_seconds=0.01,
            drain_window_seconds=0.0,
        ),
        execution_strategy=strategy_for_transport(AgentTransport.CODEX),
        evaluate_completion_fn=evaluate,
    )

    list(process_reader._run_subprocess_and_read_lines(["codex"], ctx))

    assert evaluator_called is False
    assert handle.terminated is False


def test_real_declare_complete_sentinel_stops_open_codex_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    completed = threading.Event()
    secret = "test-parent-only-secret"
    monkeypatch.setenv("RALPH_BROKER_SECRET", secret)
    session = AgentSession(
        session_id="session-1",
        run_id="run-1",
        drain="development",
        capabilities={"artifact.submit"},
        broker_secret=secret,
    )
    workspace = FsWorkspace(tmp_path)

    def live_stdout() -> Iterator[str]:
        yield '{"type":"item.completed"}\n'
        result = handle_declare_complete(session, workspace, {"summary": "done"})
        assert result.is_error is False
        assert completed.wait(timeout=0.1)

    handle = _LiveHandle(live_stdout(), completed)

    class _ProcessManager:
        def spawn(self, _argv: object, _options: object) -> _LiveHandle:
            return handle

        def register_listener(self, _listener: object) -> Callable[[], None]:
            return lambda: None

    monkeypatch.setattr(process_reader, "get_process_manager", _ProcessManager)
    ctx = AgentRunCtx(
        config=AgentConfig(cmd="codex", transport=AgentTransport.CODEX),
        show_progress=False,
        extra_env={"RALPH_MCP_RUN_ID": "run-1"},
        workspace_path=tmp_path,
        policy=TimeoutPolicy(
            idle_timeout_seconds=5.0,
            max_session_seconds=5.0,
            idle_poll_interval_seconds=0.01,
            drain_window_seconds=0.0,
        ),
        execution_strategy=strategy_for_transport(AgentTransport.CODEX),
    )

    lines = list(process_reader._run_subprocess_and_read_lines(["codex"], ctx))

    assert lines == ['{"type":"item.completed"}\n']
    assert handle.terminated is True
