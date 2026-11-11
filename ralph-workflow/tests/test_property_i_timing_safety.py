from __future__ import annotations

import importlib
import inspect

import pytest

import ralph.mcp.protocol.env as env_mod
from ralph.mcp.protocol.env import MCP_REQUEST_TIMEOUT_MS_ENV, McpEnvVar
from ralph.mcp.server import _timing_safety
from ralph.mcp.server._timing_safety import (
    CLIENT_REQUEST_TIMEOUT_MS,
    DEFAULT_CLIENT_REQUEST_TIMEOUT_MS,
    DISPATCH_CAP_MS,
    DRAIN_CEILING_MS,
    KILL_ESCALATION_CEILING_MS,
    SERVER_WORST_CASE_MS,
    timing_safety_margin_ms,
)
from ralph.timeout_defaults import (
    EXEC_MAX_TIMEOUT_MS,
    SSE_DRAIN_CEILING_MS,
)
from ralph.timeout_defaults import (
    KILL_ESCALATION_CEILING_MS as _TIMEOUT_DEFAULTS_KILL_ESCALATION_CEILING_MS,
)


def test_dispatch_cap_equals_exec_max_timeout() -> None:
    assert DISPATCH_CAP_MS == EXEC_MAX_TIMEOUT_MS


def test_drain_ceiling_is_5000() -> None:
    assert DRAIN_CEILING_MS == 5_000


def test_kill_escalation_ceiling_is_5000() -> None:
    assert KILL_ESCALATION_CEILING_MS == 5_000
    assert KILL_ESCALATION_CEILING_MS == _TIMEOUT_DEFAULTS_KILL_ESCALATION_CEILING_MS


def test_default_client_request_timeout_is_330000() -> None:
    assert DEFAULT_CLIENT_REQUEST_TIMEOUT_MS == 330_000


def test_server_worst_case_is_sum_of_constants() -> None:
    assert SERVER_WORST_CASE_MS == DISPATCH_CAP_MS + DRAIN_CEILING_MS + KILL_ESCALATION_CEILING_MS


def test_server_worst_case_less_than_client_timeout() -> None:
    assert SERVER_WORST_CASE_MS < CLIENT_REQUEST_TIMEOUT_MS
    margin = timing_safety_margin_ms()
    assert margin == CLIENT_REQUEST_TIMEOUT_MS - SERVER_WORST_CASE_MS


def test_timing_safety_margin_is_positive() -> None:
    assert timing_safety_margin_ms() > 0


def test_env_var_member_added_to_mcp_env_var() -> None:
    assert hasattr(McpEnvVar, "REQUEST_TIMEOUT_MS")
    assert McpEnvVar.REQUEST_TIMEOUT_MS == "RALPH_MCP_REQUEST_TIMEOUT_MS"
    assert McpEnvVar.REQUEST_TIMEOUT_MS in list(McpEnvVar)


def test_module_level_constant_added() -> None:
    assert hasattr(env_mod, "MCP_REQUEST_TIMEOUT_MS_ENV")
    assert env_mod.MCP_REQUEST_TIMEOUT_MS_ENV == "RALPH_MCP_REQUEST_TIMEOUT_MS"


def test_request_timeout_env_used_in_timing_safety() -> None:
    assert _timing_safety.MCP_REQUEST_TIMEOUT_MS_ENV == MCP_REQUEST_TIMEOUT_MS_ENV


def test_drain_ceiling_constant_in_timeout_defaults() -> None:
    assert SSE_DRAIN_CEILING_MS == 5_000


def test_kill_escalation_constant_in_timeout_defaults() -> None:
    assert _TIMEOUT_DEFAULTS_KILL_ESCALATION_CEILING_MS == 5_000


def test_safety_margin_is_at_least_5_seconds() -> None:
    assert timing_safety_margin_ms() >= 5_000


@pytest.mark.parametrize(
    "env_value,expected",
    [
        (None, 330_000),
        ("600000", 600_000),
    ],
)
def test_client_request_timeout_env_override(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
    expected: int,
) -> None:
    if env_value is None:
        monkeypatch.delenv("RALPH_MCP_REQUEST_TIMEOUT_MS", raising=False)
    else:
        monkeypatch.setenv("RALPH_MCP_REQUEST_TIMEOUT_MS", env_value)
    importlib.reload(_timing_safety)
    assert expected == _timing_safety.CLIENT_REQUEST_TIMEOUT_MS


def test_import_time_guard_fires_when_env_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_MCP_REQUEST_TIMEOUT_MS", "5000")
    with pytest.raises(RuntimeError, match="dispatch_cap exceeds client_request_timeout"):
        importlib.reload(_timing_safety)


def test_timing_safety_guard_uses_runtimeerror_not_assert() -> None:
    text = inspect.getsource(_timing_safety)
    assert "raise RuntimeError" in text, (
        "_timing_safety.py must contain ``raise RuntimeError`` so the "
        "import-time guard survives ``python -O``"
    )
    pre_block = text.split("SERVER_WORST_CASE_MS = ")[0]
    assert "assert DISPATCH_CAP_MS" not in pre_block, (
        "pre-SERVER_WORST_CASE_MS block must not use ``assert`` for the "
        "safety invariant; ``python -O`` would strip it"
    )
    assert "assert (" not in pre_block, (
        "pre-SERVER_WORST_CASE_MS block must not use ``assert`` for the "
        "safety invariant; ``python -O`` would strip it"
    )
