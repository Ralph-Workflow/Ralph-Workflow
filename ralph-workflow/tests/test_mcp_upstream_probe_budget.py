"""The upstream discovery probe budget must fit inside the server-readiness budget.

A Ralph MCP server subprocess enumerates every configured upstream MCP server
BEFORE it binds its HTTP port. The parent gives that subprocess
``mcp_preflight_timeout_from_env()`` to become reachable. When one upstream
probe was allowed to consume that entire window, the parent killed the child
mid-probe: the child never reached the line where it reports WHICH upstream is
unreachable, and the operator was handed a bare ``[Errno 61] Connection
refused`` for a misconfigured docs server.

The probe budget is therefore derived from -- and clamped strictly below -- the
readiness budget, so a stalled upstream always leaves the child enough time to
fail loudly and name itself.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from typing import IO

import pytest

from ralph.mcp.protocol.env import MCP_PREFLIGHT_TIMEOUT_MS_ENV, MCP_UPSTREAM_PROBE_TIMEOUT_MS_ENV
from ralph.mcp.protocol.startup import (
    mcp_preflight_timeout_from_env,
    mcp_upstream_probe_timeout_from_env,
    upstream_call_timeout_seconds,
)
from ralph.mcp.upstream.client import StdioUpstreamClient, make_upstream_client
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.process.manager import ProcessManager, ProcessManagerPolicy


def test_default_probe_budget_is_strictly_below_the_readiness_budget() -> None:
    assert mcp_upstream_probe_timeout_from_env({}) < mcp_preflight_timeout_from_env({})


def test_probe_budget_tracks_a_raised_readiness_budget() -> None:
    env = {str(MCP_PREFLIGHT_TIMEOUT_MS_ENV): "120000"}

    budget = mcp_upstream_probe_timeout_from_env(env)

    assert budget > mcp_upstream_probe_timeout_from_env({})
    assert budget < mcp_preflight_timeout_from_env(env)


def test_explicit_probe_budget_is_honoured_when_it_fits() -> None:
    env = {str(MCP_UPSTREAM_PROBE_TIMEOUT_MS_ENV): "2500"}

    assert mcp_upstream_probe_timeout_from_env(env) == timedelta(milliseconds=2500)


def test_explicit_probe_budget_is_clamped_below_the_readiness_budget() -> None:
    env = {
        str(MCP_PREFLIGHT_TIMEOUT_MS_ENV): "30000",
        str(MCP_UPSTREAM_PROBE_TIMEOUT_MS_ENV): "600000",
    }

    assert mcp_upstream_probe_timeout_from_env(env) < mcp_preflight_timeout_from_env(env)


class _RecordingUpstreamPopen:
    """A Popen stand-in that records the communicate timeout it was handed."""

    def __init__(self, pid: int, seen: dict[str, object]) -> None:
        self.pid = pid
        self._seen = seen
        self._returncode: int | None = None
        self.stdin: IO[bytes] | None = None
        self.stdout: IO[bytes] | None = BytesIO()
        self.stderr: IO[bytes] | None = BytesIO()

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._returncode = 0
        return 0

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input
        self._seen["timeout"] = timeout
        self._returncode = 0
        return b'{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n', b""

    def terminate(self) -> None:
        self._returncode = -15

    def kill(self) -> None:
        self._returncode = -9


def _stdio_client_recording_timeouts(
    monkeypatch: pytest.MonkeyPatch, seen: dict[str, object]
) -> StdioUpstreamClient:
    def factory(command: object, opts: object) -> object:
        del command, opts
        return _RecordingUpstreamPopen(pid=1, seen=seen)

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        sync_process_factory=factory,
    )
    monkeypatch.setattr(
        "ralph.mcp.upstream._stdio_upstream_client.get_process_manager",
        lambda: pm,
    )
    client = make_upstream_client(
        UpstreamMcpServer(name="docs-mcp", transport="stdio", command="fake-cmd", args=())
    )
    assert isinstance(client, StdioUpstreamClient)
    return client


def test_stdio_upstream_discovery_uses_the_bounded_probe_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    _stdio_client_recording_timeouts(monkeypatch, seen).list_tools()

    assert seen["timeout"] == pytest.approx(mcp_upstream_probe_timeout_from_env().total_seconds())


def test_stdio_upstream_tool_call_keeps_the_full_call_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only DISCOVERY runs inside the startup window; a real tool call does not.

    Shortening tool calls to the probe budget would time out slow-but-healthy
    upstream tools, which is a different failure from the startup stall.
    """
    seen: dict[str, object] = {}

    _stdio_client_recording_timeouts(monkeypatch, seen).call_tool("search", {})

    assert seen["timeout"] == pytest.approx(upstream_call_timeout_seconds("tools/call"))
    assert upstream_call_timeout_seconds("tools/call") > (
        mcp_upstream_probe_timeout_from_env().total_seconds()
    )
