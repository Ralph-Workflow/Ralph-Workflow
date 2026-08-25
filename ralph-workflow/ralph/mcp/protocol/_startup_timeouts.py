"""Startup timeout budgets for the MCP server and its upstream discovery.

Two budgets are nested, and the nesting is the whole point:

- the READINESS budget (``mcp_preflight_timeout_from_env``) is how long the
  parent waits for a freshly spawned MCP server subprocess to answer on its
  HTTP endpoint;
- the PROBE budget (``mcp_upstream_probe_timeout_from_env``) is how long ONE
  upstream MCP server gets to answer ``tools/list`` while that subprocess is
  still starting up -- upstream discovery runs BEFORE the port is bound.

They used to be equal (30s each). A single stalled upstream therefore consumed
the entire readiness window, so the parent killed the child mid-probe: the child
never reached the line that names the unreachable server, and the operator was
handed a bare ``[Errno 61] Connection refused``. Clamping the probe budget
strictly below the readiness budget guarantees the child always has time left to
fail loudly and say which upstream stalled.

This module deliberately imports nothing from :mod:`ralph.mcp.protocol.startup`
so the upstream clients -- which ``startup`` transitively imports -- can depend
on it without an import cycle. ``startup`` re-exports both functions as the
public surface.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import (
    MCP_PREFLIGHT_TIMEOUT_MS_ENV,
    MCP_UPSTREAM_PROBE_TIMEOUT_MS_ENV,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_DEFAULT_PREFLIGHT = timedelta(milliseconds=30_000)

# One upstream probe may claim at most this share of the readiness budget. Half
# leaves the child the other half to unwind, log the failure, and exit while the
# parent is still listening -- and keeps the arithmetic obvious to an operator
# who raises RALPH_MCP_PREFLIGHT_TIMEOUT_MS to accommodate a slow upstream.
_PROBE_BUDGET_SHARE = 0.5

_DISCOVERY_METHOD = "tools/list"

# Budget for an upstream tool call, which runs outside the startup window.
_UPSTREAM_CALL_TIMEOUT_SECONDS = 30.0


def _timeout_ms_from_env(env: Mapping[str, str], name: str) -> timedelta | None:
    raw = env.get(name)
    if raw is None:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return timedelta(milliseconds=max(1, parsed))


def mcp_preflight_timeout_from_env(env: Mapping[str, str] | None = None) -> timedelta:
    """Return the configured MCP preflight timeout duration."""

    env_map = os.environ if env is None else env
    return _timeout_ms_from_env(env_map, MCP_PREFLIGHT_TIMEOUT_MS_ENV) or _DEFAULT_PREFLIGHT


def mcp_upstream_probe_timeout_from_env(env: Mapping[str, str] | None = None) -> timedelta:
    """Return the per-upstream discovery budget, clamped below the readiness budget.

    ``RALPH_MCP_UPSTREAM_PROBE_TIMEOUT_MS`` lowers it explicitly. A value that
    would not leave the child room to report its own failure is clamped rather
    than honoured: to genuinely allow a slower upstream, raise
    ``RALPH_MCP_PREFLIGHT_TIMEOUT_MS`` as well, which lifts both budgets
    together.
    """

    env_map = os.environ if env is None else env
    ceiling = mcp_preflight_timeout_from_env(env_map) * _PROBE_BUDGET_SHARE
    requested = _timeout_ms_from_env(env_map, MCP_UPSTREAM_PROBE_TIMEOUT_MS_ENV)
    if requested is None:
        return ceiling
    return min(requested, ceiling)


def mcp_upstream_probe_timeout_seconds(env: Mapping[str, str] | None = None) -> float:
    """Return :func:`mcp_upstream_probe_timeout_from_env` as seconds for ``timeout=``."""

    return mcp_upstream_probe_timeout_from_env(env).total_seconds()


def upstream_call_timeout_seconds(method: str, env: Mapping[str, str] | None = None) -> float:
    """Return the bounded ``timeout=`` an upstream JSON-RPC ``method`` may claim.

    ``tools/list`` is DISCOVERY: it runs while the MCP server subprocess is
    still starting, so it is capped by the startup budget above. Every other
    method -- a real tool call -- happens long after the server is serving, and
    keeps the full call budget; shortening it would time out slow-but-healthy
    upstream tools, an unrelated failure.
    """

    if method == _DISCOVERY_METHOD:
        return mcp_upstream_probe_timeout_seconds(env)
    return _UPSTREAM_CALL_TIMEOUT_SECONDS


__all__ = [
    "mcp_preflight_timeout_from_env",
    "mcp_upstream_probe_timeout_from_env",
    "mcp_upstream_probe_timeout_seconds",
    "upstream_call_timeout_seconds",
]
