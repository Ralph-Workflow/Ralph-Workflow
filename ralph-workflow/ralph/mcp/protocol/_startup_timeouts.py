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

# Floor on the time the child needs to fail loudly after the last probe. With N
# upstreams sharing the probe budget, the child can be left with no room to
# report a stall if this safety margin is zero or the worst-case probe budget
# per upstream is below it; we bound the upstream count below so the child
# always has at least one safety margin's worth of time to surface the failure.
_PROBE_SAFETY_S: float = 2.0

# Worst-case count of upstreams at defaults (30 s preflight, 0.5 share = 15 s
# probe budget) whose per-probe budget stays at or above the safety margin and
# whose probes still leave the child at least one safety margin to fail loudly.
_MAX_UPSTREAMS_AT_DEFAULTS: int = int(
    (_DEFAULT_PREFLIGHT * _PROBE_BUDGET_SHARE - timedelta(seconds=_PROBE_SAFETY_S))
    / timedelta(seconds=_PROBE_SAFETY_S)
)

# Import-time invariant: a non-positive safety floor or a degenerate upstream
# count would silently invalidate the math above. ``if``/``raise`` (not
# ``assert``) so this holds under ``python -O`` as well as a plain import.
if not (_PROBE_SAFETY_S > 0):
    raise RuntimeError(
        f"_PROBE_SAFETY_S must be > 0, got {_PROBE_SAFETY_S!r}"
    )
if _MAX_UPSTREAMS_AT_DEFAULTS < 1:
    raise RuntimeError(
        f"_MAX_UPSTREAMS_AT_DEFAULTS must be >= 1, got {_MAX_UPSTREAMS_AT_DEFAULTS!r}"
    )

# Import-time invariant: the default preflight budget must leave room for the
# child to report a probe failure loudly. When the ceiling (preflight * share)
# is at or below _PROBE_SAFETY_S, every probe that hits the ceiling would burn
# the child's entire failure-reporting window and the supervisor would SIGKILL
# it before it could log which upstream stalled. The example in the brief:
#   PYTHONDONTWRITEBYTECODE=1 RALPH_MCP_PREFLIGHT_TIMEOUT_MS=1000 \
#     uv run python -B -O -c 'from ralph.mcp.protocol._startup_timeouts import mcp_preflight_timeout_from_env'
# must exit non-zero because 1000 ms * 0.5 = 500 ms < 2.0 s = _PROBE_SAFETY_S.
# This is tested in tests/test_startup_timeouts_invariants.py.
_DEFAULT_PREFLIGHT_BUDGET = _DEFAULT_PREFLIGHT * _PROBE_BUDGET_SHARE
if not (_DEFAULT_PREFLIGHT_BUDGET.total_seconds() > _PROBE_SAFETY_S):
    raise RuntimeError(
        f"import-time invariant violated: default preflight budget "
        f"({_DEFAULT_PREFLIGHT_BUDGET.total_seconds():.1f}s = "
        f"{_DEFAULT_PREFLIGHT.total_seconds():.0f}s * {_PROBE_BUDGET_SHARE} share) "
        f"must exceed _PROBE_SAFETY_S ({_PROBE_SAFETY_S}s) to give the child "
        f"time to report a stalled probe before the supervisor SIGKILLs it. "
        f"Raise RALPH_MCP_PREFLIGHT_TIMEOUT_MS above "
        f"{int(_PROBE_SAFETY_S / _PROBE_BUDGET_SHARE * 1000)} ms, "
        f"or lower _PROBE_BUDGET_SHARE."
    )

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
    """Return the configured MCP preflight timeout duration.

    Raises:
        RuntimeError: if the preflight is so small that the probe ceiling
            (preflight * _PROBE_BUDGET_SHARE) would be at or below
            _PROBE_SAFETY_S, leaving no time for the child to report a
            stalled upstream before the supervisor SIGKILLs it.
    """

    env_map = os.environ if env is None else env
    preflight = _timeout_ms_from_env(env_map, MCP_PREFLIGHT_TIMEOUT_MS_ENV) or _DEFAULT_PREFLIGHT
    ceiling = preflight * _PROBE_BUDGET_SHARE
    if ceiling.total_seconds() <= _PROBE_SAFETY_S:
        raise RuntimeError(
            f"preflight timeout ({preflight.total_seconds():.0f}s) yields a "
            f"probe ceiling of only {ceiling.total_seconds():.1f}s, which is "
            f"at or below _PROBE_SAFETY_S ({_PROBE_SAFETY_S}s). "
            f"Raise RALPH_MCP_PREFLIGHT_TIMEOUT_MS to at least "
            f"{int(_PROBE_SAFETY_S / _PROBE_BUDGET_SHARE * 1000)} ms, "
            f"or lower _PROBE_BUDGET_SHARE."
        )
    return preflight


def mcp_upstream_probe_timeout_from_env(env: Mapping[str, str] | None = None) -> timedelta:
    """Return the per-upstream discovery budget, clamped below the readiness budget.

    ``RALPH_MCP_UPSTREAM_PROBE_TIMEOUT_MS`` lowers it explicitly. A value that
    would not leave the child room to report its own failure raises
    ``RuntimeError``: to allow a slower upstream, raise
    ``RALPH_MCP_PREFLIGHT_TIMEOUT_MS`` as well, which lifts the ceiling.
    """

    env_map = os.environ if env is None else env
    ceiling = mcp_preflight_timeout_from_env(env_map) * _PROBE_BUDGET_SHARE
    # Raise rather than silently under-sizing: a ceiling at or below the safety
    # floor means the supervisor would SIGKILL the child before it could log
    # which upstream stalled.
    if ceiling.total_seconds() <= _PROBE_SAFETY_S:
        raise RuntimeError(
            f"probe timeout budget ({ceiling.total_seconds():.1f}s = "
            f"{mcp_preflight_timeout_from_env(env_map).total_seconds():.0f}s * "
            f"{_PROBE_BUDGET_SHARE} share) must exceed _PROBE_SAFETY_S "
            f"({_PROBE_SAFETY_S}s) to give the child time to report a stalled "
            f"upstream before the supervisor SIGKILLs it. "
            f"Raise RALPH_MCP_PREFLIGHT_TIMEOUT_MS to at least "
            f"{int(_PROBE_SAFETY_S / _PROBE_BUDGET_SHARE * 1000)} ms, "
            f"or lower _PROBE_BUDGET_SHARE."
        )
    requested = _timeout_ms_from_env(env_map, MCP_UPSTREAM_PROBE_TIMEOUT_MS_ENV)
    if requested is None:
        return ceiling
    return min(requested, ceiling)


def mcp_upstream_probe_timeout_seconds(env: Mapping[str, str] | None = None) -> float:
    """Return :func:`mcp_upstream_probe_timeout_from_env` as seconds for ``timeout=``."""

    return mcp_upstream_probe_timeout_from_env(env).total_seconds()


def mcp_discovery_budget_from_env(env: Mapping[str, str] | None = None) -> timedelta:
    """Return the aggregate first-start discovery budget.

    This is the preflight window minus the safety margin reserved for the child
    to report a failure after discovery completes, before the supervisor
    SIGKILLs it. It is strictly below ``mcp_preflight_timeout_from_env`` by
    construction.

    The discovery loop in ``UpstreamRegistry.build`` must consume this budget
    across all probes: when it expires, remaining servers are warned-and-skipped
    rather than probed, guaranteeing the worst-case discovery time is bounded
    below the preflight timeout.
    """

    preflight = mcp_preflight_timeout_from_env(env)
    return preflight - timedelta(seconds=_PROBE_SAFETY_S)


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
    "_PROBE_SAFETY_S",
    "mcp_discovery_budget_from_env",
    "mcp_preflight_timeout_from_env",
    "mcp_upstream_probe_timeout_from_env",
    "mcp_upstream_probe_timeout_seconds",
    "upstream_call_timeout_seconds",
]
