"""Regression tests for bounded startup overrun exit on stalled stage/diagnostic I/O.

Validates that when a pre-serve overrun occurs, stage-recording,
logging, and stdio-flushing operations are bounded by a strict fail-safe
timeout. A stalled or wedged filesystem (e.g. hung NFS, blocked FIFO,
unresponsive disk) or blocked stdio pipe cannot delay process termination
past the deadline before ``os._exit`` is invoked.
"""

from __future__ import annotations

import os
import sys
import threading
import time as time_module
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest

import ralph.mcp.server._pre_serve_overrun as pre_serve_overrun_mod
from ralph.config.mcp_models import McpConfig
from ralph.mcp.protocol._startup_timeouts import (
    _OVERRUN_DIAGNOSTIC_BUDGET_S,
    _PROBE_SAFETY_S,
    _check_real_startup_deadline,
    mcp_overrun_diagnostic_timeout_from_env,
    mcp_overrun_diagnostic_timeout_seconds,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._pre_serve_overrun import (
    CATALOG_PUBLISH_OVERRUN_EXIT_CODE,
    PRE_SERVE_OVERRUN_EXIT_CODE,
    SESSION_OVERRUN_EXIT_CODE,
    fail_closed_pre_serve_overrun,
)
from ralph.mcp.server._publish_catalog_bounded import publish_tool_catalog_bounded
from ralph.mcp.server._start_stage_tracker import read_last_stage
from ralph.mcp.server.runtime import McpServerExtras

if TYPE_CHECKING:
    from pathlib import Path


class _ExitInterceptedError(Exception):
    """Sentinel exception raised by mock os._exit to halt execution cleanly in test."""

    def __init__(self, code: int) -> None:
        super().__init__(f"os._exit({code})")
        self.code = code


def _mock_exit(code: int) -> NoReturn:
    raise _ExitInterceptedError(code)


def test_overrun_with_stalled_stage_recording_exits_promptly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _record_stage is wedged on a stalled filesystem, os._exit is called promptly."""
    stall_gate = threading.Event()
    stall_completed = False

    def stalled_record(root: Path, stage: str) -> None:
        nonlocal stall_completed
        stall_gate.wait(timeout=10.0)
        stall_completed = True

    monkeypatch.setattr(pre_serve_overrun_mod, "_record_stage", stalled_record)
    monkeypatch.setattr(os, "_exit", _mock_exit)

    try:
        with pytest.raises(_ExitInterceptedError) as exc_info:
            fail_closed_pre_serve_overrun(
                tmp_path,
                stage="session_ready",
                elapsed_s=1.2,
                budget_s=1.0,
                exit_code=SESSION_OVERRUN_EXIT_CODE,
                diagnostic_timeout_s=0.05,
            )
        assert exc_info.value.code == SESSION_OVERRUN_EXIT_CODE
        assert stall_completed is False, "Overrun exit must not wait for wedged stage write"
    finally:
        stall_gate.set()


def test_overrun_with_stalled_stdio_flush_exits_promptly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When stdio flush is wedged on a blocked pipe, os._exit is called promptly."""
    flush_gate = threading.Event()
    flush_completed = False

    def stalled_flush() -> None:
        nonlocal flush_completed
        flush_gate.wait(timeout=10.0)
        flush_completed = True

    monkeypatch.setattr(sys.stdout, "flush", stalled_flush)
    monkeypatch.setattr(os, "_exit", _mock_exit)

    try:
        with pytest.raises(_ExitInterceptedError) as exc_info:
            fail_closed_pre_serve_overrun(
                tmp_path,
                stage="config_loading",
                elapsed_s=1.5,
                budget_s=1.0,
                exit_code=PRE_SERVE_OVERRUN_EXIT_CODE,
                diagnostic_timeout_s=0.05,
            )
        assert exc_info.value.code == PRE_SERVE_OVERRUN_EXIT_CODE
        assert flush_completed is False, "Overrun exit must not wait for wedged stdio flush"
    finally:
        flush_gate.set()


def test_overrun_with_failing_stage_recording_suppresses_error_and_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _record_stage raises an unhandled OSError, it is suppressed and os._exit proceeds."""

    def failing_record(root: Path, stage: str) -> None:
        raise OSError("Hostile filesystem / disk read-only error")

    monkeypatch.setattr(pre_serve_overrun_mod, "_record_stage", failing_record)
    monkeypatch.setattr(os, "_exit", _mock_exit)

    with pytest.raises(_ExitInterceptedError) as exc_info:
        fail_closed_pre_serve_overrun(
            tmp_path,
            stage="config_loading",
            elapsed_s=1.3,
            budget_s=1.0,
            exit_code=PRE_SERVE_OVERRUN_EXIT_CODE,
            diagnostic_timeout_s=0.1,
        )
    assert exc_info.value.code == PRE_SERVE_OVERRUN_EXIT_CODE


def test_overrun_clean_path_records_stage_and_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under normal filesystem operation, stage is recorded and exit code is honored."""
    monkeypatch.setattr(os, "_exit", _mock_exit)

    with pytest.raises(_ExitInterceptedError) as exc_info:
        fail_closed_pre_serve_overrun(
            tmp_path,
            stage="catalog_publish",
            elapsed_s=0.6,
            budget_s=0.4,
            exit_code=CATALOG_PUBLISH_OVERRUN_EXIT_CODE,
            diagnostic_timeout_s=0.5,
        )
    assert exc_info.value.code == CATALOG_PUBLISH_OVERRUN_EXIT_CODE
    assert read_last_stage(tmp_path) == "catalog_publish"


def test_overrun_diagnostic_timeout_from_env_defaults_and_overrides() -> None:
    """mcp_overrun_diagnostic_timeout_from_env honors env overrides and safe defaults."""
    # Default without env
    assert mcp_overrun_diagnostic_timeout_from_env({}) == timedelta(
        seconds=_OVERRUN_DIAGNOSTIC_BUDGET_S
    )
    assert mcp_overrun_diagnostic_timeout_seconds({}) == _OVERRUN_DIAGNOSTIC_BUDGET_S

    # Valid override (500 ms)
    env_custom = {"RALPH_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS": "500"}
    assert mcp_overrun_diagnostic_timeout_from_env(env_custom) == timedelta(milliseconds=500)
    assert mcp_overrun_diagnostic_timeout_seconds(env_custom) == 0.5

    # Invalid string falls back to default
    env_invalid = {"RALPH_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS": "not-a-number"}
    assert mcp_overrun_diagnostic_timeout_from_env(env_invalid) == timedelta(
        seconds=_OVERRUN_DIAGNOSTIC_BUDGET_S
    )

    # Empty string falls back to default
    env_empty = {"RALPH_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS": ""}
    assert mcp_overrun_diagnostic_timeout_from_env(env_empty) == timedelta(
        seconds=_OVERRUN_DIAGNOSTIC_BUDGET_S
    )


def test_overrun_diagnostic_budget_invariants() -> None:
    """_OVERRUN_DIAGNOSTIC_BUDGET_S must be strictly positive and below _PROBE_SAFETY_S."""
    assert _OVERRUN_DIAGNOSTIC_BUDGET_S > 0
    assert _OVERRUN_DIAGNOSTIC_BUDGET_S < _PROBE_SAFETY_S


def test_slow_session_from_env_succeeds_with_default_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 1.5s slow session_from_env succeeds with default env (previously killed at 1.0s).

    Under the old contract, session_timeout_s was min(_PRE_SERVE_BUDGET_S=1.0s, remaining_s),
    killing session loading after 1.0s. Under the new contract, session loading gets
    the full remaining real startup deadline (28.0s at default), allowing slow-but-successful
    session discovery (e.g. 1.5s) to complete normally without being killed.
    """
    import ralph.mcp.server.runtime as runtime_mod

    clock_val = 0.0

    def fake_clock() -> float:
        return clock_val

    session = AgentSession(
        session_id="test-session-slow",
        run_id="test-run",
        drain="standalone",
        capabilities={"mcp"},
    )

    def slow_session_from_env(*args: object, **kwargs: object) -> AgentSession:
        nonlocal clock_val
        clock_val += 1.5  # Simulate 1.5s elapsed during session_from_env
        return session

    monkeypatch.setattr(runtime_mod, "_clock", fake_clock)
    monkeypatch.setattr("ralph.mcp.protocol._startup_timeouts._time_module.monotonic", fake_clock)
    monkeypatch.setattr(runtime_mod, "session_from_env", slow_session_from_env)

    # Mock server so run_standalone_server does not block or start real network listener
    class _MockServer:
        def __init__(self) -> None:
            self.run_called = False

        def run(self, *args: object, **kwargs: object) -> None:
            self.run_called = True

    mock_server = _MockServer()
    build_called_with_session: list[AgentSession] = []

    def mock_build(
        workspace_root: Path,
        *,
        extras: McpServerExtras | None = None,
        **kwargs: object,
    ) -> _MockServer:
        if extras is not None and isinstance(extras.session, AgentSession):
            build_called_with_session.append(extras.session)
        return mock_server

    monkeypatch.setattr(runtime_mod, "build_standalone_http_server", mock_build)

    # Must complete cleanly without being killed by overrun helper
    runtime_mod.run_standalone_server(tmp_path, env={})
    assert build_called_with_session == [session]
    assert mock_server.run_called is True


def test_hung_session_from_env_fails_closed_before_preflight_minus_safety(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hung session_from_env fails closed with last_stage='session_ready' before preflight - _PROBE_SAFETY_S."""
    import concurrent.futures

    import ralph.mcp.server.runtime as runtime_mod

    # Synchronize clocks to exact 0.0 so remaining_s math is exact
    monkeypatch.setattr(runtime_mod, "_clock", lambda: 0.0)
    monkeypatch.setattr("ralph.mcp.protocol._startup_timeouts._time_module.monotonic", lambda: 0.0)

    captured_timeout: list[float] = []

    def mock_future_result(
        self: concurrent.futures.Future[object], timeout: float | None = None
    ) -> object:
        if timeout is not None:
            captured_timeout.append(timeout)
        raise concurrent.futures.TimeoutError("Session loading hung")

    monkeypatch.setattr(concurrent.futures.Future, "result", mock_future_result)
    monkeypatch.setattr(os, "_exit", _mock_exit)

    default_preflight_s = 30.0
    expected_deadline_s = default_preflight_s - _PROBE_SAFETY_S  # 28.0s

    with pytest.raises(_ExitInterceptedError) as exc_info:
        runtime_mod.run_standalone_server(tmp_path, env={})

    assert exc_info.value.code == SESSION_OVERRUN_EXIT_CODE
    # Captured timeout must match preflight - _PROBE_SAFETY_S (28.0s at default)
    assert len(captured_timeout) >= 1
    assert captured_timeout[0] == expected_deadline_s
    assert captured_timeout[0] < default_preflight_s
    # Stage file must attribute failure to session_ready
    assert read_last_stage(tmp_path) == "session_ready"


def test_pre_serve_io_taking_1_5s_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-serve I/O taking 1.5s succeeds under the real remaining startup deadline."""
    import ralph.mcp.server.runtime as runtime_mod
    from ralph.config.mcp_models import McpConfig

    clock_val = 0.0

    def fake_clock() -> float:
        return clock_val

    session = AgentSession(
        session_id="test-session-preserve",
        run_id="test-run",
        drain="standalone",
        capabilities={"mcp"},
    )

    def slow_load_mcp_config(*args: object, **kwargs: object) -> McpConfig:
        nonlocal clock_val
        clock_val += 1.5  # Simulate 1.5s pre-serve I/O
        return McpConfig()

    monkeypatch.setattr(runtime_mod, "_clock", fake_clock)
    monkeypatch.setattr("ralph.mcp.protocol._startup_timeouts._time_module.monotonic", fake_clock)
    monkeypatch.setattr(runtime_mod, "load_mcp_config", slow_load_mcp_config)

    # Call _poll_bounded_pre_serve with t0=0.0 and default env
    ws, cfg = runtime_mod._poll_bounded_pre_serve(
        workspace_root=tmp_path,
        effective_session=session,
        workspace=None,
        extras=McpServerExtras(),
        env_map={},
        t0=0.0,
    )
    assert ws is not None
    assert cfg is not None
    # Verify clock advanced by 1.5s and real deadline check succeeds
    assert clock_val == 1.5
    _check_real_startup_deadline("config_loaded", 0.0, {})


def test_pre_serve_future_timeout_uses_remaining_headroom_not_one_second_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-serve Future.result timeout uses remaining headroom (~28s), not the old 1s cap."""
    from concurrent.futures import Future

    import ralph.mcp.server.runtime as runtime_mod

    captured_timeouts: list[float | None] = []
    real_result = Future.result

    def spy_result(self: Future[object], timeout: float | None = None) -> object:
        captured_timeouts.append(timeout)
        return real_result(self, timeout=timeout)

    monkeypatch.setattr(runtime_mod, "_clock", lambda: 0.0)
    monkeypatch.setattr("ralph.mcp.protocol._startup_timeouts._time_module.monotonic", lambda: 0.0)
    monkeypatch.setattr(Future, "result", spy_result)

    session = AgentSession(
        session_id="test-session-preserve-cap",
        run_id="test-run",
        drain="standalone",
        capabilities={"mcp"},
    )

    ws, cfg = runtime_mod._poll_bounded_pre_serve(
        workspace_root=tmp_path,
        effective_session=session,
        workspace=None,
        extras=McpServerExtras(mcp_config=McpConfig()),
        env_map={},
        t0=0.0,
    )
    assert ws is not None
    assert cfg is not None
    assert len(captured_timeouts) == 1
    timeout = captured_timeouts[0]
    assert timeout is not None
    # Must use remaining headroom (~28.0s at defaults), strictly greater than the old 1.0s cap
    assert timeout > 1.0
    assert abs(timeout - 28.0) < 1.0


def test_catalog_publish_slow_but_answers_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catalog-publish slow-but-answers (e.g. 1.5s) succeeds under remaining deadline."""
    import ralph.mcp.server._publish_catalog_bounded as pub_mod

    clock_val = 100.0

    def fake_monotonic() -> float:
        return clock_val

    monkeypatch.setattr(time_module, "monotonic", fake_monotonic)
    monkeypatch.setattr("ralph.mcp.server._publish_catalog_bounded.time.monotonic", fake_monotonic)

    publish_called = False

    def slow_publish(root: Path, cat: dict[str, list[object]]) -> None:
        nonlocal publish_called, clock_val
        publish_called = True
        clock_val += 1.5  # Simulate 1.5s publish I/O

    monkeypatch.setattr(pub_mod, "publish_tool_catalog_to_parent", slow_publish)

    # t0 is 100.0, so at start remaining_s is whole_budget (~29.8s > 1.5s)
    publish_tool_catalog_bounded(
        workspace_root=tmp_path,
        catalog={},
        env_map={},
        t0=100.0,
    )
    assert publish_called is True


def test_registry_read_slow_but_answers_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry-read slow-but-answers (e.g. 1.5s) succeeds without raising McpServerError."""
    import ralph.mcp.tools.bridge._registry as reg_mod
    from ralph.config.mcp_models import McpConfig
    from ralph.mcp.protocol.session import AgentSession

    clock_val = 0.0

    def fake_clock() -> float:
        return clock_val

    monkeypatch.setattr(reg_mod, "_clock", fake_clock)
    monkeypatch.setattr("ralph.mcp.protocol._startup_timeouts._time_module.monotonic", fake_clock)

    session = AgentSession(
        session_id="test-session-reg",
        run_id="test-run",
        drain="standalone",
        capabilities={"mcp"},
    )

    warned: list[tuple[str, float, float]] = []

    def mock_warn(stage: str, elapsed_s: float, budget_s: float) -> None:
        warned.append((stage, elapsed_s, budget_s))

    monkeypatch.setattr(reg_mod, "_warn_over_budget", mock_warn)

    # Set clock to 1.5s so elapsed is 1.5s > budget_s (0.4s)
    clock_val = 1.5

    # Prior to the fix, elapsed > budget_s raised McpServerError.
    # Now it emits a diagnostic warning and returns the built registry.
    reg = reg_mod.build_ralph_tool_registry(
        session=session,
        workspace=None,
        mcp_config=McpConfig(),
        t0=0.0,
        budget_s=0.4,
        timeout_s=28.0,
    )
    assert reg is not None
    assert len(warned) == 1
    assert warned[0][0] == "registry_built"
    assert warned[0][1] == 1.5
    assert warned[0][2] == 0.4
    # And real startup deadline is NOT exceeded (1.5s <= 28.0s)
    _check_real_startup_deadline("registry_built", 0.0, {})


def test_runtime_read_timeout_selection_slow_read_succeeds_with_configured_400ms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With configured 400ms timeout and a 1.5s successful read, runtime read succeeds."""
    import ralph.mcp.server.runtime as runtime_mod
    import ralph.mcp.tools.bridge._registry as reg_mod
    from ralph.config.mcp_models import McpConfig
    from ralph.workspace.fs import FsWorkspace

    clock_val = 0.0

    def fake_clock() -> float:
        return clock_val

    monkeypatch.setattr(runtime_mod, "_clock", fake_clock)
    monkeypatch.setattr(reg_mod, "_clock", fake_clock)
    monkeypatch.setattr("ralph.mcp.protocol._startup_timeouts._time_module.monotonic", fake_clock)

    session = AgentSession(
        session_id="test-session-slow-read",
        run_id="test-run",
        drain="standalone",
        capabilities={"WorkspaceRead"},
    )

    class _SlowWorkspace(FsWorkspace):
        def read(self, path: str) -> str:
            nonlocal clock_val
            clock_val += 1.5  # Simulate 1.5s read
            return super().read(path)

    slow_ws = _SlowWorkspace(tmp_path)
    env_map = {"RALPH_MCP_REGISTRY_READ_TIMEOUT_MS": "400"}

    # Under old code, effective_read_timeout_s was min(28.0, 0.4) = 0.4s, killing the read.
    # Under new contract, effective_read_timeout_s is remaining real deadline (28.0s),
    # so the 1.5s slow-but-responsive read succeeds.
    server = runtime_mod._build_mcp_registry_and_server(
        effective_session=session,
        workspace=slow_ws,
        upstream_reg=None,
        upstream_servers=(),
        mcp_cfg=McpConfig(),
        activity_relay_sender=None,
        t0=0.0,
        env_map=env_map,
    )
    assert server is not None
    assert clock_val == 1.5
    _check_real_startup_deadline("registry_built", 0.0, env_map)


def test_runtime_read_timeout_selection_hung_read_fails_at_remaining_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hung read fails closed at remaining deadline with last_stage='registry_built'."""
    import ralph.mcp.server.runtime as runtime_mod
    import ralph.mcp.tools.bridge._registry as reg_mod
    from ralph.config.mcp_models import McpConfig
    from ralph.mcp.server._mcp_server_error import McpServerError
    from ralph.workspace.fs import FsWorkspace

    clock_val = 27.95

    def fake_clock() -> float:
        return clock_val

    monkeypatch.setattr(runtime_mod, "_clock", fake_clock)
    monkeypatch.setattr(reg_mod, "_clock", fake_clock)
    monkeypatch.setattr("ralph.mcp.protocol._startup_timeouts._time_module.monotonic", fake_clock)

    class _HungWorkspace(FsWorkspace):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.hang_event = threading.Event()

        def read(self, path: str) -> str:
            self.hang_event.wait(timeout=5.0)
            return super().read(path)

    hung_ws = _HungWorkspace(tmp_path)
    session = AgentSession(
        session_id="test-session-hung-read",
        run_id="test-run",
        drain="standalone",
        capabilities={"WorkspaceRead"},
    )

    try:
        with pytest.raises(McpServerError) as exc_info:
            runtime_mod._build_mcp_registry_and_server(
                effective_session=session,
                workspace=hung_ws,
                upstream_reg=None,
                upstream_servers=(),
                mcp_cfg=McpConfig(),
                activity_relay_sender=None,
                t0=0.0,
                env_map={},
            )

        err = exc_info.value
        assert err.last_stage == "registry_built"
        assert err.elapsed_s is not None
        assert err.elapsed_s >= 27.95
    finally:
        hung_ws.hang_event.set()


def test_blocked_stage_recorder_fails_closed_before_preflight_with_headroom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked stage recorder causes child startup to fail closed before preflight with headroom."""
    import ralph.mcp.server.runtime as runtime_mod

    # Synchronize clocks to exact 0.0 so remaining_s math is exact
    monkeypatch.setattr(runtime_mod, "_clock", lambda: 0.0)
    monkeypatch.setattr("ralph.mcp.protocol._startup_timeouts._time_module.monotonic", lambda: 0.0)

    captured_timeout: list[float] = []

    def blocked_record(root: Path, stage: str, *, timeout_s: float | None = None) -> None:
        if timeout_s is not None:
            captured_timeout.append(timeout_s)
        raise TimeoutError("Stage recorder wedged on stalled filesystem")

    monkeypatch.setattr("ralph.mcp.server._pre_serve_overrun._record_stage", blocked_record)
    monkeypatch.setattr(os, "_exit", _mock_exit)

    default_preflight_s = 30.0
    expected_deadline_s = default_preflight_s - _PROBE_SAFETY_S  # 28.0s

    captured_exit: list[int] = []

    def mock_fail_closed(
        workspace_root: Path,
        *,
        stage: str,
        elapsed_s: float,
        budget_s: float,
        exit_code: int,
        diagnostic_timeout_s: float | None = None,
    ) -> NoReturn:
        captured_exit.append(exit_code)
        raise _ExitInterceptedError(exit_code)

    monkeypatch.setattr(
        "ralph.mcp.server._pre_serve_overrun.fail_closed_pre_serve_overrun", mock_fail_closed
    )

    with pytest.raises(_ExitInterceptedError) as exc_info:
        runtime_mod.run_standalone_server(tmp_path, env={})

    assert exc_info.value.code == PRE_SERVE_OVERRUN_EXIT_CODE
    assert captured_exit == [PRE_SERVE_OVERRUN_EXIT_CODE]
    # Captured timeout must match preflight - _PROBE_SAFETY_S (28.0s at default)
    assert len(captured_timeout) >= 1
    assert captured_timeout[0] == expected_deadline_s
    assert captured_timeout[0] < default_preflight_s
    # Headroom before preflight must be at least _PROBE_SAFETY_S (2.0s)
    available_headroom = default_preflight_s - captured_timeout[0]
    assert available_headroom == _PROBE_SAFETY_S


def test_slow_stage_recorder_succeeds_without_premature_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage recorder taking 1.5s succeeds without premature failure (within 28s deadline)."""
    import ralph.mcp.server.runtime as runtime_mod
    from ralph.mcp.server._start_stage_tracker import record as real_record

    clock_val = 0.0

    def fake_clock() -> float:
        return clock_val

    session = AgentSession(
        session_id="test-session-slow-record",
        run_id="test-run",
        drain="standalone",
        capabilities={"mcp"},
    )

    def slow_record(root: Path, stage: str, *, timeout_s: float | None = None) -> None:
        nonlocal clock_val
        clock_val += 1.5  # Simulate 1.5s stage recording
        real_record(root, stage)

    def mock_session_from_env(
        *,
        declared_agent_transport: str | None = None,
        workspace_root: Path | None = None,
    ) -> AgentSession:
        return session

    class _MockServer:
        def __init__(self) -> None:
            self.run_called = False

        def run(self, *args: object, **kwargs: object) -> None:
            self.run_called = True

    mock_server = _MockServer()

    def mock_build(
        workspace_root: Path,
        *,
        extras: McpServerExtras | None = None,
        **kwargs: object,
    ) -> _MockServer:
        return mock_server

    monkeypatch.setattr(runtime_mod, "_clock", fake_clock)
    monkeypatch.setattr("ralph.mcp.protocol._startup_timeouts._time_module.monotonic", fake_clock)
    monkeypatch.setattr("ralph.mcp.server._pre_serve_overrun._record_stage", slow_record)
    monkeypatch.setattr(runtime_mod, "session_from_env", mock_session_from_env)
    monkeypatch.setattr(runtime_mod, "build_standalone_http_server", mock_build)

    # Must complete cleanly without premature failure
    runtime_mod.run_standalone_server(tmp_path, env={})
    assert mock_server.run_called is True
    assert read_last_stage(tmp_path) == "session_ready"
