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
from datetime import timedelta
from typing import TYPE_CHECKING, NoReturn

import pytest

import ralph.mcp.server._pre_serve_overrun as pre_serve_overrun_mod
from ralph.mcp.protocol._startup_timeouts import (
    _OVERRUN_DIAGNOSTIC_BUDGET_S,
    _PROBE_SAFETY_S,
    mcp_overrun_diagnostic_timeout_from_env,
    mcp_overrun_diagnostic_timeout_seconds,
)
from ralph.mcp.server._pre_serve_overrun import (
    CATALOG_PUBLISH_OVERRUN_EXIT_CODE,
    PRE_SERVE_OVERRUN_EXIT_CODE,
    SESSION_OVERRUN_EXIT_CODE,
    fail_closed_pre_serve_overrun,
)
from ralph.mcp.server._start_stage_tracker import read_last_stage

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
