"""Regression tests for bounded startup overrun exit on stalled stage/diagnostic I/O.

Validates that when a pre-serve overrun occurs, stage-recording,
logging, and stdio-flushing operations are bounded by a strict fail-safe
timeout. A stalled or wedged filesystem (e.g. hung NFS, blocked FIFO,
unresponsive disk) or blocked stdio pipe cannot delay process termination
past the deadline before ``os._exit`` is invoked.

The tests drive the production code through PUBLIC seams only:

- the injected monotonic clock (``clock_fn``) of the startup deadline
  helpers, so no test patches ``time.monotonic``;
- the public stage recorder (the ``stage_recorder`` argument and
  :func:`stage_recorder_override`), so no test patches the private
  ``_record_stage`` module attribute;
- the public composition seams of :mod:`ralph.mcp.server.runtime`
  (``session_from_env``, ``load_mcp_config``,
  ``build_standalone_http_server``), which the runtime documents as
  replaceable by callers and test doubles.

Only standard-library attributes (``os._exit``, ``sys.stdout.flush``,
``concurrent.futures.Future.result``) are intercepted, always through a
scoped ``unittest.mock.patch`` so the restoration is automatic.
"""

from __future__ import annotations

import concurrent.futures
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn
from unittest import mock

import pytest

import ralph.mcp.server.runtime as runtime_mod
from ralph.config.mcp_models import McpConfig
from ralph.mcp.protocol._startup_timeouts import (
    check_real_startup_deadline,
    mcp_overrun_diagnostic_timeout_from_env,
    mcp_overrun_diagnostic_timeout_seconds,
    mcp_preflight_timeout_from_env,
    mcp_probe_safety_s,
    mcp_real_startup_deadline_from_env,
    mcp_startup_now,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._pre_serve_overrun import (
    CATALOG_PUBLISH_OVERRUN_EXIT_CODE,
    PRE_SERVE_OVERRUN_EXIT_CODE,
    SESSION_OVERRUN_EXIT_CODE,
    StageEventRecorder,
    exit_handler_override,
    fail_closed_pre_serve_overrun,
    run_pre_serve_bounded,
    stage_recorder_override,
)
from ralph.mcp.server._publish_catalog_bounded import publish_tool_catalog_bounded
from ralph.mcp.server._start_stage_tracker import read_last_stage
from ralph.mcp.server._start_stage_tracker import record as real_record_stage
from ralph.mcp.tools.bridge import ToolBridge, build_ralph_tool_registry

if TYPE_CHECKING:
    from ralph.mcp.server.runtime import McpServerExtras


class _ExitInterceptedError(Exception):
    """Sentinel exception raised by the intercepted ``os._exit`` to halt execution cleanly."""

    def __init__(self, code: int) -> None:
        super().__init__(f"os._exit({code})")
        self.code = code


def _mock_exit(code: int) -> NoReturn:
    raise _ExitInterceptedError(code)


class _MockServer:
    """Stand-in for the standalone HTTP server (public composition seam)."""

    def __init__(self) -> None:
        self.run_called = False

    def run(self, *args: object, **kwargs: object) -> None:
        """Record that the runtime asked the server to serve."""
        self.run_called = True


def _spy_future_result(
    captured: list[float | None],
) -> object:
    """Return a ``Future.result`` spy that records the requested timeout."""

    real_result = concurrent.futures.Future.result

    def spy_result(self: concurrent.futures.Future[object], timeout: float | None = None) -> object:
        captured.append(timeout)
        return real_result(self, timeout=timeout)

    return spy_result


def test_overrun_with_stalled_stage_recording_exits_promptly(tmp_path: Path) -> None:
    """When the stage recorder is wedged on a stalled filesystem, os._exit is called promptly."""
    stall_gate = threading.Event()
    stall_completed = False

    def stalled_record(root: Path, stage: str, *, timeout_s: float | None = None) -> None:
        nonlocal stall_completed
        stall_gate.wait(timeout=10.0)
        stall_completed = True

    try:
        with (
            exit_handler_override(_mock_exit),
            pytest.raises(_ExitInterceptedError) as exc_info,
        ):
            fail_closed_pre_serve_overrun(
                tmp_path,
                stage="session_ready",
                elapsed_s=1.2,
                budget_s=1.0,
                exit_code=SESSION_OVERRUN_EXIT_CODE,
                diagnostic_timeout_s=0.05,
                stage_recorder=stalled_record,
            )
        assert exc_info.value.code == SESSION_OVERRUN_EXIT_CODE
        assert stall_completed is False, "Overrun exit must not wait for wedged stage write"
    finally:
        stall_gate.set()


def test_overrun_with_stalled_stdio_flush_exits_promptly(tmp_path: Path) -> None:
    """When stdio flush is wedged on a blocked pipe, os._exit is called promptly."""
    flush_gate = threading.Event()
    flush_completed = False

    def stalled_flush() -> None:
        nonlocal flush_completed
        flush_gate.wait(timeout=10.0)
        flush_completed = True

    try:
        with (
            exit_handler_override(_mock_exit),
            mock.patch.object(sys.stdout, "flush", stalled_flush),
            pytest.raises(_ExitInterceptedError) as exc_info,
        ):
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


def test_overrun_with_failing_stage_recording_suppresses_error_and_exits(tmp_path: Path) -> None:
    """When the stage recorder raises an unhandled OSError, it is suppressed and os._exit proceeds."""

    def failing_record(root: Path, stage: str, *, timeout_s: float | None = None) -> None:
        raise OSError("Hostile filesystem / disk read-only error")

    with (
        exit_handler_override(_mock_exit),
        pytest.raises(_ExitInterceptedError) as exc_info,
    ):
        fail_closed_pre_serve_overrun(
            tmp_path,
            stage="config_loading",
            elapsed_s=1.3,
            budget_s=1.0,
            exit_code=PRE_SERVE_OVERRUN_EXIT_CODE,
            diagnostic_timeout_s=0.1,
            stage_recorder=failing_record,
        )
    assert exc_info.value.code == PRE_SERVE_OVERRUN_EXIT_CODE


def test_overrun_clean_path_records_stage_and_exits(tmp_path: Path) -> None:
    """Under normal filesystem operation, stage is recorded and exit code is honored."""
    with (
        exit_handler_override(_mock_exit),
        pytest.raises(_ExitInterceptedError) as exc_info,
    ):
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
    default_budget_s = mcp_overrun_diagnostic_timeout_from_env({}).total_seconds()

    # Default without env
    assert default_budget_s == mcp_overrun_diagnostic_timeout_seconds({})
    assert default_budget_s > 0

    # Valid override (500 ms)
    env_custom = {"RALPH_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS": "500"}
    assert mcp_overrun_diagnostic_timeout_from_env(env_custom).total_seconds() == 0.5
    assert mcp_overrun_diagnostic_timeout_seconds(env_custom) == 0.5

    # Invalid string falls back to default
    env_invalid = {"RALPH_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS": "not-a-number"}
    assert mcp_overrun_diagnostic_timeout_from_env(env_invalid).total_seconds() == default_budget_s

    # Empty string falls back to default
    env_empty = {"RALPH_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS": ""}
    assert mcp_overrun_diagnostic_timeout_from_env(env_empty).total_seconds() == default_budget_s


def test_overrun_diagnostic_budget_invariants() -> None:
    """The overrun diagnostic budget must be strictly positive and below the probe-safety floor."""
    budget_s = mcp_overrun_diagnostic_timeout_from_env({}).total_seconds()
    assert budget_s > 0
    assert budget_s < mcp_probe_safety_s()


def test_slow_session_from_env_succeeds_with_default_env(tmp_path: Path) -> None:
    """A slow session succeeds with the default env (it is not killed at the legacy 1.0s cap).

    Under the old contract, ``session_timeout_s`` was
    ``min(_PRE_SERVE_BUDGET_S=1.0s, remaining_s)``, killing session loading
    after 1.0s. Under the new contract, session loading gets the full
    remaining real startup deadline (28.0s at default), so a
    slow-but-successful session (e.g. 1.5s) completes normally.
    """
    session = AgentSession(
        session_id="test-session-slow",
        run_id="test-run",
        drain="standalone",
        capabilities={"mcp"},
    )

    captured_timeouts: list[float | None] = []

    def mock_session_from_env(*args: object, **kwargs: object) -> AgentSession:
        return session

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

    with (
        mock.patch("ralph.mcp.server.runtime.session_from_env", mock_session_from_env),
        mock.patch("ralph.mcp.server.runtime.build_standalone_http_server", mock_build),
        mock.patch.object(
            concurrent.futures.Future, "result", _spy_future_result(captured_timeouts)
        ),
    ):
        # Must complete cleanly without being killed by overrun helper
        runtime_mod.run_standalone_server(tmp_path, env={})

    assert build_called_with_session == [session]
    assert mock_server.run_called is True
    # The session future is bounded by the remaining real startup deadline
    # (~28.0s), NOT by the legacy 1.0s per-stage cap.
    assert len(captured_timeouts) == 1
    session_timeout_s = captured_timeouts[0]
    assert session_timeout_s is not None
    assert session_timeout_s == pytest.approx(28.0, abs=0.5)
    assert session_timeout_s > 1.0


def test_hung_session_from_env_fails_closed_before_preflight_minus_safety(tmp_path: Path) -> None:
    """Hung session_from_env fails closed with last_stage='session_ready' before preflight - safety."""
    captured_timeout: list[float] = []

    def mock_future_result(
        self: concurrent.futures.Future[object], timeout: float | None = None
    ) -> object:
        if timeout is not None:
            captured_timeout.append(timeout)
        raise concurrent.futures.TimeoutError("Session loading hung")

    with (
        mock.patch.object(concurrent.futures.Future, "result", mock_future_result),
        exit_handler_override(_mock_exit),
        pytest.raises(_ExitInterceptedError) as exc_info,
    ):
        runtime_mod.run_standalone_server(tmp_path, env={})

    assert exc_info.value.code == SESSION_OVERRUN_EXIT_CODE
    # The captured timeout must match preflight - probe-safety (28.0s at default)
    assert len(captured_timeout) >= 1
    expected_deadline_s = mcp_real_startup_deadline_from_env({})
    assert captured_timeout[0] == pytest.approx(expected_deadline_s, abs=0.5)
    assert captured_timeout[0] < mcp_preflight_timeout_from_env({}).total_seconds()
    assert captured_timeout[0] > 1.0
    # Stage file must attribute failure to session_ready
    assert read_last_stage(tmp_path) == "session_ready"


def test_pre_serve_io_taking_1_5s_succeeds(tmp_path: Path) -> None:
    """Pre-serve I/O taking 1.5s succeeds under the real remaining startup deadline."""
    captured_timeouts: list[float | None] = []
    t0 = mcp_startup_now()

    def do_pre_serve() -> tuple[Path, McpConfig]:
        return (tmp_path, McpConfig())

    with mock.patch.object(
        concurrent.futures.Future, "result", _spy_future_result(captured_timeouts)
    ):
        ws, cfg = run_pre_serve_bounded(
            tmp_path,
            do_pre_serve,
            stage="config_loading",
            env_map={},
            t0=t0,
        )

    assert ws is not None
    assert cfg is not None
    # The pre-serve critical path is bounded by the remaining real startup
    # deadline (~28s), not by the legacy 1.0s per-stage cap.
    assert len(captured_timeouts) == 1
    pre_serve_timeout_s = captured_timeouts[0]
    assert pre_serve_timeout_s is not None
    assert pre_serve_timeout_s == pytest.approx(28.0, abs=0.5)
    assert pre_serve_timeout_s > 1.0
    # A stage that took 1.5s is still inside the 28.0s fatal envelope.
    check_real_startup_deadline("config_loaded", 0.0, {}, clock_fn=lambda: 1.5)


def test_pre_serve_future_timeout_uses_remaining_headroom_not_one_second_cap(
    tmp_path: Path,
) -> None:
    """Pre-serve Future.result timeout uses remaining headroom (~28s), not the old 1s cap."""
    captured_timeouts: list[float | None] = []
    t0 = mcp_startup_now()

    def do_pre_serve() -> tuple[Path, McpConfig]:
        return (tmp_path, McpConfig())

    with mock.patch.object(
        concurrent.futures.Future, "result", _spy_future_result(captured_timeouts)
    ):
        ws, cfg = run_pre_serve_bounded(
            tmp_path,
            do_pre_serve,
            stage="config_loading",
            env_map={},
            t0=t0,
        )

    assert ws is not None
    assert cfg is not None
    assert len(captured_timeouts) == 1
    timeout_s = captured_timeouts[0]
    assert timeout_s is not None
    # Must use remaining headroom (~28.0s at defaults), strictly greater than the old 1.0s cap
    assert timeout_s > 1.0
    assert abs(timeout_s - 28.0) < 1.0


def test_catalog_publish_slow_but_answers_succeeds(tmp_path: Path) -> None:
    """Catalog-publish slow-but-answers (e.g. 1.5s) succeeds under remaining deadline."""
    captured_timeouts: list[float | None] = []

    with mock.patch.object(
        concurrent.futures.Future, "result", _spy_future_result(captured_timeouts)
    ):
        publish_tool_catalog_bounded(
            workspace_root=tmp_path,
            catalog={},
            env_map={},
        )

    # The publish is bounded by the real remaining startup deadline (28.0s),
    # not by the whole-startup envelope (29.8s) and not by a 1s cap.
    assert len(captured_timeouts) == 1
    timeout_s = captured_timeouts[0]
    assert timeout_s is not None
    assert timeout_s == pytest.approx(28.0, abs=0.5)
    assert timeout_s < 29.8
    assert timeout_s > 1.0


def test_registry_read_slow_but_answers_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    """Registry-read slow-but-answers (e.g. 1.5s) succeeds without raising McpServerError."""
    clock_val = 0.0

    def fake_clock() -> float:
        return clock_val

    session = AgentSession(
        session_id="test-session-reg",
        run_id="test-run",
        drain="standalone",
        capabilities={"mcp"},
    )

    # Set clock to 1.5s so elapsed is 1.5s > budget_s (0.4s)
    clock_val = 1.5

    # Prior to the fix, elapsed > budget_s raised McpServerError.
    # Now it emits a diagnostic warning and returns the built registry.
    reg = build_ralph_tool_registry(
        session=session,
        workspace=None,
        mcp_config=McpConfig(),
        t0=0.0,
        budget_s=0.4,
        timeout_s=28.0,
        clock_fn=fake_clock,
    )
    assert reg is not None
    warning = capsys.readouterr().err
    assert "registry_built" in warning
    assert "elapsed_s=1.500s" in warning
    assert "budget_s=0.400s" in warning
    # And real startup deadline is NOT exceeded (1.5s <= 28.0s)
    check_real_startup_deadline("registry_built", 0.0, {}, clock_fn=fake_clock)


def test_runtime_read_timeout_selection_slow_read_succeeds_with_configured_400ms(
    tmp_path: Path,
) -> None:
    """With configured 400ms timeout, the runtime read is bounded by the real deadline, not 400ms."""
    from ralph.workspace.fs import FsWorkspace

    session = AgentSession(
        session_id="test-session-slow-read",
        run_id="test-run",
        drain="standalone",
        capabilities={"WorkspaceRead"},
    )

    captured_kwargs: list[dict[str, object]] = []

    def spy_build(
        session: object,
        workspace: object,
        *,
        upstream_registry: object = None,
        mcp_config: McpConfig | None = None,
        t0: float | None = None,
        budget_s: float | None = None,
        timeout_s: float | None = None,
        clock_fn: Callable[[], float] | None = None,
    ) -> ToolBridge:
        captured_kwargs.append({"timeout_s": timeout_s})
        return build_ralph_tool_registry(
            session,
            workspace,
            mcp_config=mcp_config,
            t0=t0,
            budget_s=budget_s,
            timeout_s=timeout_s,
            clock_fn=clock_fn,
        )

    slow_ws = FsWorkspace(tmp_path)
    env_map = {"RALPH_MCP_REGISTRY_READ_TIMEOUT_MS": "400"}
    t0 = mcp_startup_now()

    # Under old code, effective_read_timeout_s was min(28.0, 0.4) = 0.4s, killing the read.
    # Under the new contract, the read is bounded by the remaining real deadline
    # (28.0s), so a slow-but-responsive read succeeds.
    with mock.patch("ralph.mcp.server.runtime.build_ralph_tool_registry", spy_build):
        server = runtime_mod._build_mcp_registry_and_server(
            effective_session=session,
            workspace=slow_ws,
            upstream_reg=None,
            upstream_servers=(),
            mcp_cfg=McpConfig(),
            activity_relay_sender=None,
            t0=t0,
            env_map=env_map,
        )

    assert server is not None
    assert len(captured_kwargs) == 1
    read_timeout_s = captured_kwargs[0]["timeout_s"]
    assert isinstance(read_timeout_s, float)
    assert read_timeout_s == pytest.approx(28.0, abs=0.5)
    # The configured 400ms is a diagnostic ceiling only: it must NOT cap the read.
    assert read_timeout_s > 0.4
    # And the fatal startup deadline is not exceeded.
    check_real_startup_deadline("registry_built", t0, env_map)


def test_runtime_read_timeout_selection_hung_read_fails_at_remaining_deadline(
    tmp_path: Path,
) -> None:
    """Hung read fails closed at remaining deadline with last_stage='registry_built'."""
    from ralph.mcp.server._mcp_server_error import McpServerError
    from ralph.workspace.fs import FsWorkspace

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

    # 27.95s of the 28.0s startup envelope is already spent, so the read is
    # bounded by the 0.05s that remain -- no clock patching required.
    t0 = mcp_startup_now() - 27.95

    try:
        with pytest.raises(McpServerError) as exc_info:
            runtime_mod._build_mcp_registry_and_server(
                effective_session=session,
                workspace=hung_ws,
                upstream_reg=None,
                upstream_servers=(),
                mcp_cfg=McpConfig(),
                activity_relay_sender=None,
                t0=t0,
                env_map={},
            )

        err = exc_info.value
        assert err.last_stage == "registry_built"
        assert err.elapsed_s is not None
        assert err.elapsed_s >= 27.95
    finally:
        hung_ws.hang_event.set()


def test_blocked_stage_recorder_fails_closed_before_preflight_with_headroom(
    tmp_path: Path,
) -> None:
    """A blocked stage recorder causes child startup to fail closed before preflight with headroom."""
    captured_timeout: list[float] = []

    def blocked_record(root: Path, stage: str, *, timeout_s: float | None = None) -> None:
        if timeout_s is not None:
            captured_timeout.append(timeout_s)
        raise TimeoutError("Stage recorder wedged on stalled filesystem")

    with (
        stage_recorder_override(blocked_record),
        exit_handler_override(_mock_exit),
        pytest.raises(_ExitInterceptedError) as exc_info,
    ):
        runtime_mod.run_standalone_server(tmp_path, env={})

    assert exc_info.value.code == PRE_SERVE_OVERRUN_EXIT_CODE
    # The stage write was bounded by the remaining real startup deadline.
    assert len(captured_timeout) >= 1
    expected_deadline_s = mcp_real_startup_deadline_from_env({})
    assert captured_timeout[0] == pytest.approx(expected_deadline_s, abs=0.5)
    assert captured_timeout[0] < mcp_preflight_timeout_from_env({}).total_seconds()
    # Headroom before preflight must be at least the probe-safety floor (2.0s)
    available_headroom = mcp_preflight_timeout_from_env({}).total_seconds() - captured_timeout[0]
    assert available_headroom == pytest.approx(2.0, abs=0.5)


def test_slow_stage_recorder_succeeds_without_premature_failure(tmp_path: Path) -> None:
    """Stage recorder taking 1.5s succeeds without premature failure (within 28s deadline)."""
    session = AgentSession(
        session_id="test-session-slow-record",
        run_id="test-run",
        drain="standalone",
        capabilities={"mcp"},
    )

    clock_val = 0.0

    def fake_clock() -> float:
        return clock_val

    observed = StageEventRecorder()
    write_timeouts: list[float | None] = []

    def slow_record(root: Path, stage: str, *, timeout_s: float | None = None) -> None:
        nonlocal clock_val
        clock_val += 1.5  # Simulate 1.5s of stage-recording work.
        write_timeouts.append(timeout_s)
        observed(root, stage, timeout_s=timeout_s)
        real_record_stage(root, stage, timeout_s=timeout_s)

    mock_server = _MockServer()

    def mock_build(workspace_root: Path, **kwargs: object) -> _MockServer:
        return mock_server

    def mock_session_from_env(*args: object, **kwargs: object) -> AgentSession:
        return session

    with (
        mock.patch("ralph.mcp.server.runtime.session_from_env", mock_session_from_env),
        mock.patch("ralph.mcp.server.runtime.build_standalone_http_server", mock_build),
        stage_recorder_override(slow_record),
    ):
        # Must complete cleanly without premature failure
        runtime_mod.run_standalone_server(tmp_path, env={})

    assert mock_server.run_called is True
    # The recorded stage sequence is observable through the public recorder.
    assert observed.last_stage == "session_ready"
    # The stage write is bounded by the remaining real startup deadline
    # (~28s), not by the legacy 1.0s per-stage cap.
    assert len(write_timeouts) >= 1
    assert write_timeouts[0] is not None
    assert write_timeouts[0] == pytest.approx(28.0, abs=0.5)
    assert write_timeouts[0] > 1.0
    # The stage marker also reached the on-disk stage file.
    assert read_last_stage(tmp_path) == "session_ready"


def test_pre_serve_bounded_timeout_fails_closed_with_overrun(tmp_path: Path) -> None:
    """run_pre_serve_bounded times out when action stalls, triggering fail-closed overrun."""

    def hung_action() -> None:
        raise concurrent.futures.TimeoutError("Stalled pre-serve I/O")

    with (
        exit_handler_override(_mock_exit),
        mock.patch.object(
            concurrent.futures.Future,
            "result",
            side_effect=concurrent.futures.TimeoutError("Stalled pre-serve I/O"),
        ),
        pytest.raises(_ExitInterceptedError) as exc_info,
    ):
        run_pre_serve_bounded(
            tmp_path,
            hung_action,
            stage="config_loading",
            env_map={},
            t0=mcp_startup_now(),
        )

    assert exc_info.value.code == PRE_SERVE_OVERRUN_EXIT_CODE
