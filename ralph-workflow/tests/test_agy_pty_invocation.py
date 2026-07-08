"""Focused regressions for AGY PTY routing and completion detection."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from ralph.agents.invoke import (
    InvokeOptions,
    _clear_session_completion_sentinel,
    invoke_agent,
    run_pty_and_read_lines,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_prompt(tmp_path: Path, text: str = "hello") -> Path:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text(text, encoding="utf-8")
    return prompt_file


def test_agy_invoke_uses_pty_not_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = _write_prompt(tmp_path)
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    pty_called = False

    def fake_run_pty_and_read_lines(
        cmd: object,
        ctx: SimpleNamespace,
        extras: object = None,
    ) -> object:
        nonlocal pty_called
        del cmd, extras
        pty_called = True
        assert ctx.workspace_path == tmp_path
        yield "Task declared complete: session_id=test, summary=done, timestamp=1\n"

    def fake_run_subprocess_and_read_lines(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("subprocess must not be called for AGY")

    monkeypatch.setattr("ralph.agents.invoke.run_pty_and_read_lines", fake_run_pty_and_read_lines)
    monkeypatch.setattr(
        "ralph.agents.invoke.run_subprocess_and_read_lines",
        fake_run_subprocess_and_read_lines,
    )
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.setattr("ralph.agents.invoke.load_existing_agy_upstream_servers", lambda _path: ())

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False, workspace_path=tmp_path),
        )
    )

    assert pty_called


def test_agy_invoke_writes_workspace_mcp_config_when_endpoint_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = _write_prompt(tmp_path)
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    config_path = tmp_path / "mcp_config.json"
    monkeypatch.setattr("ralph.mcp.transport.agy._agy_global_config_path", lambda: config_path)
    endpoint = "http://127.0.0.1:9999/mcp"
    seen_config_at_launch = False

    def fake_run_pty_and_read_lines(
        cmd: object,
        ctx: SimpleNamespace,
        extras: object = None,
    ) -> object:
        del cmd, extras
        nonlocal seen_config_at_launch
        seen_config_at_launch = config_path.exists()
        assert ctx.workspace_path == tmp_path
        yield "Task declared complete: session_id=test, summary=done, timestamp=1\n"

    monkeypatch.setattr("ralph.agents.invoke.run_pty_and_read_lines", fake_run_pty_and_read_lines)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.setattr("ralph.agents.invoke.load_existing_agy_upstream_servers", lambda _path: ())

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): endpoint},
            ),
        )
    )

    assert seen_config_at_launch
    assert not config_path.exists()


def test_agy_invoke_completes_when_completion_signal_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = _write_prompt(tmp_path)
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    endpoint = "http://127.0.0.1:9999/mcp"
    captured_explicit_completion_seen: list[bool] = []

    class _FakeHandle:
        pid = 123

        def __enter__(self) -> _FakeHandle:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            del exc_type, exc, tb

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

        def terminate(self, grace_period_s: float = 0.5) -> None:
            del grace_period_s

    class _FakeProcessManager:
        def spawn_pty(self, *args: object, **kwargs: object) -> _FakeHandle:
            del args, kwargs
            return _FakeHandle()

    class _FakePtyLineReader:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def read_lines(self) -> object:
            yield "Task declared complete: session_id=test, summary=done, timestamp=1\n"

    class _FakePostExitWatchdog:
        def __init__(self, policy: object, clock: object) -> None:
            del policy, clock

        def wait_for_process_exit(self, is_exited_fn: object) -> object:
            del is_exited_fn
            return object()

    def fake_check_process_result(
        handle: object,
        agent_name: str,
        parsed_output: list[str],
        options: SimpleNamespace,
        *,
        _clock: object,
    ) -> None:
        del handle, agent_name, parsed_output, _clock
        captured_explicit_completion_seen.append(options.explicit_completion_seen)

    def fake_run_subprocess_and_read_lines(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("subprocess must not be called for AGY")

    def fake_get_process_manager() -> _FakeProcessManager:
        return _FakeProcessManager()

    def fake_agy_workspace_mcp_endpoint(*_args: object, **_kwargs: object) -> object:
        return contextlib.nullcontext()

    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.get_process_manager",
        fake_get_process_manager,
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.PtyLineReader",
        _FakePtyLineReader,
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.PostExitWatchdog",
        _FakePostExitWatchdog,
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner._check_process_result",
        fake_check_process_result,
    )
    monkeypatch.setattr(
        "ralph.agents.invoke.agy_workspace_mcp_endpoint",
        fake_agy_workspace_mcp_endpoint,
    )
    monkeypatch.setattr(
        "ralph.agents.invoke.run_subprocess_and_read_lines",
        fake_run_subprocess_and_read_lines,
    )
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.setattr("ralph.agents.invoke.load_existing_agy_upstream_servers", lambda _path: ())

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_ENDPOINT_ENV): endpoint},
            ),
        )
    )

    assert captured_explicit_completion_seen == [True]


def test_agy_invoke_skips_mcp_context_when_no_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = _write_prompt(tmp_path)
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    config_path = tmp_path / "mcp_config.json"
    monkeypatch.setattr("ralph.mcp.transport.agy._agy_global_config_path", lambda: config_path)

    def fake_run_pty_and_read_lines(
        cmd: object,
        ctx: SimpleNamespace,
        extras: object = None,
    ) -> object:
        del cmd, extras
        assert ctx.workspace_path == tmp_path
        yield "Task declared complete: session_id=test, summary=done, timestamp=1\n"

    def fake_run_subprocess_and_read_lines(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("subprocess must not be called for AGY")

    monkeypatch.setattr("ralph.agents.invoke.run_pty_and_read_lines", fake_run_pty_and_read_lines)
    monkeypatch.setattr(
        "ralph.agents.invoke.run_subprocess_and_read_lines",
        fake_run_subprocess_and_read_lines,
    )
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.setattr("ralph.agents.invoke.load_existing_agy_upstream_servers", lambda _path: ())

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(show_progress=False, workspace_path=tmp_path),
        )
    )

    assert not config_path.exists()


def test_agy_invoke_uses_run_id_as_expected_session_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompt_file = _write_prompt(tmp_path)
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    captured_expected_session_ids: list[str | None] = []
    run_id = "run-123"

    def fake_run_pty_and_read_lines(
        cmd: object,
        ctx: SimpleNamespace,
        extras: object = None,
    ) -> object:
        del cmd, ctx
        captured_expected_session_ids.append(getattr(extras, "expected_session_id", None))
        yield "Task declared complete: session_id=test, summary=done, timestamp=1\n"

    def fake_run_subprocess_and_read_lines(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("subprocess must not be called for AGY")

    monkeypatch.setattr("ralph.agents.invoke.run_pty_and_read_lines", fake_run_pty_and_read_lines)
    monkeypatch.setattr(
        "ralph.agents.invoke.run_subprocess_and_read_lines",
        fake_run_subprocess_and_read_lines,
    )
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    monkeypatch.setattr("ralph.agents.invoke.load_existing_agy_upstream_servers", lambda _path: ())

    list(
        invoke_agent(
            config,
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={str(MCP_RUN_ID_ENV): run_id},
            ),
        )
    )

    assert captured_expected_session_ids == [run_id]


def test_clear_session_completion_sentinel_only_deletes_own_run(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    sentinel_a = agent_dir / "completion_seen_run-a.json"
    sentinel_b = agent_dir / "completion_seen_run-b.json"
    sentinel_a.write_text('{"run_id": "run-a"}', encoding="utf-8")
    sentinel_b.write_text('{"run_id": "run-b"}', encoding="utf-8")

    _clear_session_completion_sentinel(tmp_path, "run-b")

    assert sentinel_a.exists()
    assert not sentinel_b.exists()


def test_ansi_wrapped_completion_marker_detected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ansi_line = (
        "Task decl\x1b[32mare\x1b[0md complete: session_id=test, summary=done, timestamp=1\n"
    )
    captured_completion_seen: list[bool] = []

    class _FakeHandle:
        pid = 123

        def __enter__(self) -> _FakeHandle:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            del exc_type, exc, tb

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

        def terminate(self, grace_period_s: float = 0.5) -> None:
            del grace_period_s

    class _FakeProcessManager:
        def spawn_pty(self, *args: object, **kwargs: object) -> _FakeHandle:
            del args, kwargs
            return _FakeHandle()

    class _FakePtyLineReader:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def read_lines(self) -> object:
            yield ansi_line

    class _FakePostExitWatchdog:
        def __init__(self, policy: object, clock: object) -> None:
            del policy, clock

        def wait_for_process_exit(self, is_exited_fn: object) -> object:
            del is_exited_fn
            return object()

    def fake_check_process_result(
        handle: object,
        agent_name: str,
        parsed_output: list[str],
        options: SimpleNamespace,
        *,
        _clock: object,
    ) -> None:
        del handle, agent_name, parsed_output, _clock
        captured_completion_seen.append(options.explicit_completion_seen)

    def fake_get_process_manager() -> _FakeProcessManager:
        return _FakeProcessManager()

    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.get_process_manager",
        fake_get_process_manager,
    )
    monkeypatch.setattr("ralph.agents.invoke._pty_runner.PtyLineReader", _FakePtyLineReader)
    monkeypatch.setattr("ralph.agents.invoke._pty_runner.PostExitWatchdog", _FakePostExitWatchdog)
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner._check_process_result",
        fake_check_process_result,
    )

    ctx = SimpleNamespace(
        clock=FakeClock(),
        workspace_path=tmp_path,
        extra_env={},
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        show_progress=False,
        policy=SimpleNamespace(process_exit_wait_seconds=0.1),
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
        monitor=None,
        required_artifact=None,
        evaluate_completion_fn=lambda *args, **kwargs: None,
    )

    lines = list(run_pty_and_read_lines(["claude", "--print"], cast("Any", ctx)))

    assert lines == [ansi_line]
    assert captured_completion_seen == [True]


def test_run_pty_tears_down_live_process_when_iterator_is_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Closing the public PTY iterator must not leave the child tree alive."""
    teardown_calls: list[int] = []

    class _FakeHandle:
        pid = 4242

        def __init__(self) -> None:
            self.terminate_calls: list[float] = []

        def __enter__(self) -> _FakeHandle:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            del exc_type, exc, tb

        def poll(self) -> None:
            return None

        def terminate(self, grace_period_s: float = 0.5) -> None:
            self.terminate_calls.append(grace_period_s)

    handle = _FakeHandle()

    class _FakeProcessManager:
        def spawn_pty(self, *args: object, **kwargs: object) -> _FakeHandle:
            del args, kwargs
            return handle

    class _FakePtyLineReader:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        @property
        def completion_exit_sent(self) -> bool:
            return False

        def read_lines(self) -> object:
            yield "Nanocoder banner\n"

    def fake_teardown_subtree(pid: int) -> None:
        teardown_calls.append(pid)

    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.get_process_manager",
        lambda: _FakeProcessManager(),
    )
    monkeypatch.setattr("ralph.agents.invoke._pty_runner.PtyLineReader", _FakePtyLineReader)
    monkeypatch.setattr("ralph.agents.invoke._pty_runner.teardown_subtree", fake_teardown_subtree)

    ctx = SimpleNamespace(
        clock=FakeClock(),
        workspace_path=tmp_path,
        extra_env={},
        config=AgentConfig(cmd="nanocoder", transport=AgentTransport.NANOCODER),
        show_progress=False,
        policy=SimpleNamespace(process_exit_wait_seconds=0.1),
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
        monitor=None,
        required_artifact=None,
        evaluate_completion_fn=lambda *args, **kwargs: None,
    )

    iterator = run_pty_and_read_lines(["nanocoder"], cast("Any", ctx))
    assert next(iterator) == "Nanocoder banner\n"

    iterator.close()

    assert handle.terminate_calls == [0.5]
    assert teardown_calls == [4242]
