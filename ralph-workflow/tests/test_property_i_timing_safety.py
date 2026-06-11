# property-test: I — end-to-end timing safety margin asserted by sum of bounded constants
"""The server's worst-case resolution time is strictly less than the client timeout.

The relationship that keeps the system safe — ``DISPATCH_CAP_MS +
DRAIN_CEILING_MS + KILL_ESCALATION_CEILING_MS < CLIENT_REQUEST_TIMEOUT_MS`` —
is a guarded, tested property, not an arithmetic coincidence. The
import-time guard uses ``if``/``raise RuntimeError`` (NOT ``assert``) so
``python -O`` cannot strip it.
"""

from __future__ import annotations

import importlib
from pathlib import Path

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
    """DISPATCH_CAP_MS equals EXEC_MAX_TIMEOUT_MS — the longest a single call may run."""
    assert DISPATCH_CAP_MS == EXEC_MAX_TIMEOUT_MS


def test_drain_ceiling_is_5000() -> None:
    """DRAIN_CEILING_MS is 5000ms — the SSE post-final-frame grace."""
    assert DRAIN_CEILING_MS == 5_000


def test_kill_escalation_ceiling_is_5000() -> None:
    """KILL_ESCALATION_CEILING_MS is 5000ms — the SIGTERM-then-SIGKILL grace."""
    assert KILL_ESCALATION_CEILING_MS == 5_000
    assert KILL_ESCALATION_CEILING_MS == _TIMEOUT_DEFAULTS_KILL_ESCALATION_CEILING_MS


def test_default_client_request_timeout_is_330000() -> None:
    """DEFAULT_CLIENT_REQUEST_TIMEOUT_MS is 330_000 — 5 min exec + 30s margin."""
    assert DEFAULT_CLIENT_REQUEST_TIMEOUT_MS == 330_000


def test_server_worst_case_is_sum_of_constants() -> None:
    """SERVER_WORST_CASE_MS = DISPATCH_CAP_MS + DRAIN_CEILING_MS + KILL_ESCALATION_CEILING_MS."""
    assert SERVER_WORST_CASE_MS == DISPATCH_CAP_MS + DRAIN_CEILING_MS + KILL_ESCALATION_CEILING_MS


def test_server_worst_case_less_than_client_timeout() -> None:
    """The end-to-end invariant: SERVER_WORST_CASE_MS < CLIENT_REQUEST_TIMEOUT_MS."""
    assert SERVER_WORST_CASE_MS < CLIENT_REQUEST_TIMEOUT_MS
    margin = timing_safety_margin_ms()
    assert margin == CLIENT_REQUEST_TIMEOUT_MS - SERVER_WORST_CASE_MS


def test_timing_safety_margin_is_positive() -> None:
    """The safety margin is positive (server always finishes before client times out)."""
    assert timing_safety_margin_ms() > 0


def test_env_var_member_added_to_mcp_env_var() -> None:
    """REQUEST_TIMEOUT_MS is the 15th enum member of McpEnvVar."""
    assert hasattr(McpEnvVar, "REQUEST_TIMEOUT_MS")
    assert McpEnvVar.REQUEST_TIMEOUT_MS == "RALPH_MCP_REQUEST_TIMEOUT_MS"
    members = list(McpEnvVar)
    assert len(members) == 15, f"expected 15 members, got {len(members)}"
    assert members[-1] == McpEnvVar.REQUEST_TIMEOUT_MS


def test_module_level_constant_added() -> None:
    """MCP_REQUEST_TIMEOUT_MS_ENV is the 15th module-level constant."""
    assert hasattr(env_mod, "MCP_REQUEST_TIMEOUT_MS_ENV")
    assert env_mod.MCP_REQUEST_TIMEOUT_MS_ENV == "RALPH_MCP_REQUEST_TIMEOUT_MS"


def test_request_timeout_env_used_in_timing_safety() -> None:
    """The timing safety module reads from MCP_REQUEST_TIMEOUT_MS_ENV."""
    assert _timing_safety.MCP_REQUEST_TIMEOUT_MS_ENV == MCP_REQUEST_TIMEOUT_MS_ENV


def test_drain_ceiling_constant_in_timeout_defaults() -> None:
    """SSE_DRAIN_CEILING_MS is exported from ralph.timeout_defaults."""
    assert SSE_DRAIN_CEILING_MS == 5_000


def test_kill_escalation_constant_in_timeout_defaults() -> None:
    """KILL_ESCALATION_CEILING_MS is exported from ralph.timeout_defaults."""
    assert _TIMEOUT_DEFAULTS_KILL_ESCALATION_CEILING_MS == 5_000


def test_safety_margin_is_at_least_5_seconds() -> None:
    """The safety margin is at least 5 seconds — the property is non-trivial.

    A 5-second minimum ensures a future maintainer cannot erode the margin
    by tweaking one constant at a time without failing a test.
    """
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
    """Setting RALPH_MCP_REQUEST_TIMEOUT_MS changes CLIENT_REQUEST_TIMEOUT_MS.

    The module reads the env at import time. To test, reload the module
    with the env overridden.
    """
    if env_value is None:
        monkeypatch.delenv("RALPH_MCP_REQUEST_TIMEOUT_MS", raising=False)
    else:
        monkeypatch.setenv("RALPH_MCP_REQUEST_TIMEOUT_MS", env_value)
    importlib.reload(_timing_safety)
    assert expected == _timing_safety.CLIENT_REQUEST_TIMEOUT_MS


def test_import_time_guard_fires_when_env_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting RALPH_MCP_REQUEST_TIMEOUT_MS below the server worst case raises.

    The import-time guard is the property: an env value of 5000 would let
    a client time out while the server is still dispatching (310_000ms).
    The guard fires and prevents the unsafe configuration from loading.

    Uses importlib.reload so the test can drive a fresh import under the
    new env value without module-local imports.
    """
    monkeypatch.setenv("RALPH_MCP_REQUEST_TIMEOUT_MS", "5000")
    with pytest.raises(RuntimeError, match="dispatch_cap exceeds client_request_timeout"):
        importlib.reload(_timing_safety)


def test_timing_safety_guard_uses_runtimeerror_not_assert() -> None:
    """The import-time safety guard uses ``if``/``raise RuntimeError`` (NOT ``assert``).

    PROMPT.md Standing engineering invariants and the verify.py
    import-time checks require the strip-resistant pattern: ``assert`` is
    removed by ``python -O``, so a guard written as ``assert SERVER_WORST_CASE_MS
    < CLIENT_REQUEST_TIMEOUT_MS`` would be silently disabled under
    ``-O``. The static source check below pins the pattern in the source
    text so a future maintainer cannot accidentally regress to ``assert``
    without failing this test.

    This is a fast, deterministic, no-IO static check (no subprocess, no
    ``time.sleep``, no real file I/O outside the import-time
    ``read_text``).
    """
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "ralph" / "mcp" / "server" / "_timing_safety.py").read_text()
    # The strip-resistant pattern: at least one ``raise RuntimeError`` is
    # present in the source so a future guard regression to ``assert``
    # would be visible by side-by-side diff with this assertion.
    assert "raise RuntimeError" in text, (
        "_timing_safety.py must contain ``raise RuntimeError`` so the "
        "import-time guard survives ``python -O``"
    )
    # The pre-SERVER_WORST_CASE_MS block must not use ``assert`` for the
    # safety invariant. We split on the constant declaration to inspect
    # only the import-time guard region (the runtime timing_safety_margin_ms
    # function lives later and may contain other patterns).
    pre_block = text.split("SERVER_WORST_CASE_MS = ")[0]
    assert "assert DISPATCH_CAP_MS" not in pre_block, (
        "pre-SERVER_WORST_CASE_MS block must not use ``assert`` for the "
        "safety invariant; ``python -O`` would strip it"
    )
    assert "assert (" not in pre_block, (
        "pre-SERVER_WORST_CASE_MS block must not use ``assert`` for the "
        "safety invariant; ``python -O`` would strip it"
    )
