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

Stage vocabulary tracked by :func:`record_stage_elapsed`:

- ``config_loaded`` — configuration parsed; bounded by ``_CONFIG_LOAD_BUDGET_S``;
- ``upstream_catalog_or_discovery_done`` — upstream catalog read or discovery
  finished; bounded by ``_REGISTRY_BUILD_BUDGET_S``;
- ``ready_to_serve`` — the port is bound and the server answers requests;
  bounded by ``_READY_TO_SERVE_BUDGET_S``.

The aggregate spawn-to-``tools/list`` bound is the discovery budget
(``mcp_discovery_budget_from_env`` = preflight - ``_PROBE_SAFETY_S``) and is
NOT the sum of the per-stage budgets: it is enforced by the shared monotonic
deadline consumed across all probes (see ``UpstreamRegistry.build``), bounded
above by the preflight timeout. The per-stage budgets below are individual
upper bounds on how long ONE stage may take; they do not add up to the
aggregate.

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
from ralph.mcp.server._mcp_server_error import McpServerError

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
    raise RuntimeError(f"_PROBE_SAFETY_S must be > 0, got {_PROBE_SAFETY_S!r}")
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

# Individual upper bounds on how long ONE startup stage may take. These are
# per-stage envelopes, not an aggregate: the aggregate spawn-to-tools/list
# bound is the discovery budget enforced by the shared deadline, NOT the sum
# of these.
_CONFIG_LOAD_BUDGET_S: float = 1.0
_REGISTRY_BUILD_BUDGET_S: float = 0.4
_READY_TO_SERVE_BUDGET_S: float = 0.4


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


def record_stage_elapsed(stage: str, elapsed_s: float, *, budget_s: float) -> None:
    """Enforce a per-stage startup budget; raise ``McpServerError`` when exceeded.

    Fake-clock friendly by design: the caller measures ``elapsed_s`` (with its
    own clock) and passes it in; this function never reads ``time.monotonic``,
    so tests control time entirely.
    """

    if elapsed_s > budget_s:
        raise McpServerError(
            f"startup stage {stage!r} took {elapsed_s:.3f}s, over its budget of {budget_s:.3f}s",
            last_stage=stage,
            elapsed_s=elapsed_s,
            restart_count=0,
        )


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


# Import-time invariant: a *configured* preflight (one set in the actual
# process environment at module load) must also clear the safety floor.
# ``from ralph.mcp.protocol._startup_timeouts import …`` is the operator's
# only chance to discover the configuration is bad before the supervisor
# SIGKILLs the child mid-probe; the runtime check inside
# ``mcp_preflight_timeout_from_env`` is too late because the bridge
# constructor reads the function lazily. This block is ``if``/``raise``
# so the check holds under ``python -O`` (regression of the D-02
# import-time invariant: a sub-``_PROBE_SAFETY_S`` ceiling configured
# in the environment must abort the import itself, not just the next
# function call). Absent env var -> default, which already passes the
# existing module-level check above.
_configured_preflight: timedelta = (
    _timeout_ms_from_env(os.environ, MCP_PREFLIGHT_TIMEOUT_MS_ENV) or _DEFAULT_PREFLIGHT
)
_configured_ceiling: timedelta = _configured_preflight * _PROBE_BUDGET_SHARE
if not (_configured_ceiling.total_seconds() > _PROBE_SAFETY_S):
    raise RuntimeError(
        f"import-time invariant violated: configured "
        f"RALPH_MCP_PREFLIGHT_TIMEOUT_MS "
        f"({_configured_preflight.total_seconds() * 1000:.0f}ms = "
        f"{_configured_preflight.total_seconds():.0f}s) yields a probe ceiling of "
        f"only {_configured_ceiling.total_seconds():.1f}s, which is at or below "
        f"_PROBE_SAFETY_S ({_PROBE_SAFETY_S}s). "
        f"Raise RALPH_MCP_PREFLIGHT_TIMEOUT_MS to at least "
        f"{int(_PROBE_SAFETY_S / _PROBE_BUDGET_SHARE * 1000)} ms, "
        f"or unset it to use the default {_DEFAULT_PREFLIGHT.total_seconds():.0f}s."
    )


# Import-time invariant: the discovery budget must stay strictly below the
# preflight timeout (``if``/``raise``, not ``assert``, so it holds under
# ``python -O``). With defaults this is 28s < 30s. Placed after the function
# definitions because it calls them.
if not (
    mcp_discovery_budget_from_env() < mcp_preflight_timeout_from_env()
):  # pragma: no cover - guards future edits to the budget math
    raise RuntimeError(
        "import-time invariant violated: mcp_discovery_budget_from_env() "
        "must be strictly less than mcp_preflight_timeout_from_env()"
    )


def mcp_registry_built_budget_from_env(
    env: Mapping[str, str] | None = None,
) -> float:
    """Return the cumulative budget ceiling up through the registry_built stage."""
    return (
        _CONFIG_LOAD_BUDGET_S
        + mcp_discovery_budget_from_env(env).total_seconds()
        + _REGISTRY_BUILD_BUDGET_S
    )


def mcp_whole_startup_budget_from_env(
    env: Mapping[str, str] | None = None,
) -> float:
    """Return the whole startup envelope budget ceiling across all startup stages."""
    return mcp_registry_built_budget_from_env(env) + _READY_TO_SERVE_BUDGET_S


_CONFIG_LOAD_CUMULATIVE_S: float = _CONFIG_LOAD_BUDGET_S
_REGISTRY_BUILT_CUMULATIVE_S: float = mcp_registry_built_budget_from_env()
_WHOLE_STARTUP_BUDGET_S: float = mcp_whole_startup_budget_from_env()
_READY_TO_SERVE_CUMULATIVE_S: float = _WHOLE_STARTUP_BUDGET_S

if not (mcp_preflight_timeout_from_env().total_seconds() > _WHOLE_STARTUP_BUDGET_S):
    raise RuntimeError(
        f"import-time invariant violated: sum of per-stage startup budgets "
        f"({_WHOLE_STARTUP_BUDGET_S:.3f}s) must be < "
        f"mcp_preflight_timeout_from_env() "
        f"({mcp_preflight_timeout_from_env().total_seconds():.3f}s). "
        f"Lower one of the per-stage constants or raise RALPH_MCP_PREFLIGHT_TIMEOUT_MS."
    )


__all__ = [
    "_CONFIG_LOAD_BUDGET_S",
    "_CONFIG_LOAD_CUMULATIVE_S",
    "_PROBE_SAFETY_S",
    "_READY_TO_SERVE_BUDGET_S",
    "_READY_TO_SERVE_CUMULATIVE_S",
    "_REGISTRY_BUILD_BUDGET_S",
    "_REGISTRY_BUILT_CUMULATIVE_S",
    "_WHOLE_STARTUP_BUDGET_S",
    "mcp_discovery_budget_from_env",
    "mcp_preflight_timeout_from_env",
    "mcp_registry_built_budget_from_env",
    "mcp_upstream_probe_timeout_from_env",
    "mcp_upstream_probe_timeout_seconds",
    "mcp_whole_startup_budget_from_env",
    "upstream_call_timeout_seconds",
]
