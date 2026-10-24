"""Startup timeout budgets for the MCP server subprocess and its parent-owned listening socket.

R1 (parent-owned socket): the child serves on a socket the parent bound and handed off via
``MCP_INHERITED_FD_ENV``; the child never rebinds. The pre-serve path below is bounded
against hung filesystems and slow configuration: synchronous stage writes are bounded at stage
boundaries, and overrun diagnostics are isolated behind a strict fail-safe timeout so a
wedged filesystem cannot starve the readiness budget before the socket is adopted.

Two budgets are nested, and the nesting is the whole point:

- the READINESS budget (``mcp_preflight_timeout_from_env``) is how long the
  parent waits for a freshly spawned MCP server subprocess to answer on its
  HTTP endpoint;
- the PROBE budget (``mcp_upstream_probe_timeout_from_env``) is how long ONE
  upstream MCP server gets to answer ``tools/list`` while that subprocess is
  still starting up.

They used to be equal (30s each). A single stalled upstream therefore consumed
the entire readiness window, so the parent killed the child mid-probe: the child
never reached the line that names the unreachable server, and the operator was
handed a bare ``[Errno 61] Connection refused``. Clamping the probe budget
strictly below the readiness budget guarantees the child always has time left to
fail loudly and say which upstream stalled.

DIAGNOSTIC vs DEADLINE split:

- Per-stage budgets are diagnostic warnings recorded in sibling JSONL ``.overruns`` files:
  ``record_stage_elapsed`` is DIAGNOSTIC ONLY. It logs a structured WARNING for
  per-stage overruns and appends an entry to the sibling JSONL ``.overruns`` file
  (``<stage_file_path>.overruns``) via :func:`record_overrun`, but never raises.
  It returns ``None`` on overrun so callers can distinguish logged-overrun from clean
  completion.
- The real fatal deadline is ``mcp_preflight_timeout_from_env() - _PROBE_SAFETY_S``:
  :func:`check_real_startup_deadline` is the ONLY fatal check during startup. It
  enforces the preflight envelope (default: 28.0s = 30.0s preflight - 2.0s probe-safety
  margin) and raises ``McpServerError`` when cumulative elapsed time exceeds it.

CLOCK SEAM: the deadline helpers measure elapsed time with an INJECTED monotonic
clock (:data:`ClockFn`). Production callers omit ``clock_fn`` and get the module
clock :func:`mcp_startup_now`; a caller that needs a deterministic timeline injects
its own clock instead of patching ``time.monotonic``. The same rule holds for
:func:`record_stage_elapsed`, which never reads a clock at all: the caller measures
the elapsed seconds and passes them in.

BUDGET ACCESSORS: the budget constants are private implementation details of the
envelope arithmetic; the public accessors :func:`mcp_probe_safety_s`,
:func:`mcp_probe_budget_share`, :func:`mcp_max_upstreams_at_defaults`, and
:func:`mcp_stage_budgets` expose the same numbers to operators and tests without
exposing the module internals.

Stage vocabulary tracked by :func:`record_stage_elapsed`. For every stage
below, the per-stage budget is **DIAGNOSTIC ONLY** (a structured WARNING
plus a JSONL ``.overruns`` entry — never a fatal check). The ONLY fatal
startup envelope is :func:`mcp_real_startup_deadline_from_env`
(preflight minus :data:`_PROBE_SAFETY_S`, default 28.0 s), enforced by
:func:`check_real_startup_deadline`.

- ``env_loaded`` — environment loaded; no per-stage budget (instant);
- ``session_ready`` — session initialised; no per-stage budget (instant);
- ``config_loading`` — config being loaded; per-stage budget is
  :data:`_PRE_SERVE_BUDGET_S` (DIAGNOSTIC ONLY). The operational bound
  is the remaining real startup deadline
  (:func:`mcp_real_startup_deadline_from_env`), allowing slow-but-
  successful pre-serve operations to complete within the startup
  envelope. The legacy pre-serve budget constant is queryable via
  :func:`mcp_pre_serve_budget_from_env` and is retained for diagnostic
  stage reporting and compatibility;
- ``config_loaded`` — configuration parsed; per-stage budget is
  :data:`_CONFIG_LOAD_BUDGET_S` (DIAGNOSTIC ONLY). The cumulative
  constant :data:`_CONFIG_LOAD_CUMULATIVE_S` is an import-time
  invariant input only, not a runtime per-stage budget; the only
  runtime bound is :func:`mcp_real_startup_deadline_from_env`;
- ``upstream_catalog_or_discovery_done`` — upstream catalog read or
  discovery finished; per-stage budget is
  :func:`mcp_discovery_budget_from_env` (DIAGNOSTIC ONLY);
- ``registry_built`` — registry built; per-stage budget is
  :data:`_REGISTRY_BUILD_BUDGET_S` (DIAGNOSTIC ONLY). The
  :func:`mcp_registry_built_budget_from_env` accessor is the
  diagnostic accessor only;
- ``ready_to_serve`` — the port is bound and the server answers
  requests; per-stage budget is :data:`_READY_TO_SERVE_BUDGET_S`
  (DIAGNOSTIC ONLY). The :func:`mcp_whole_startup_budget_from_env`
  accessor is the diagnostic accessor only.

The real startup deadline is ``mcp_preflight_timeout_from_env - _PROBE_SAFETY_S``
(default: 28.0s), which is strictly below the preflight ceiling (30.0s). Both
the aggregate discovery loop and the bootstrap serving architecture operationally
enforce that spawn-to-serving tools/list completes strictly below the preflight
timeout by construction, guarded by import-time invariants. Synchronous stage-file
persistence is boundary-checked at stage transitions, and the overrun exit path
bounds diagnostic and stage-recording I/O so stalled filesystem operations cannot
hold a live child past the preflight deadline.

This module deliberately imports nothing from :mod:`ralph.mcp.protocol.startup`
so the upstream clients -- which ``startup`` transitively imports -- can depend
on it without an import cycle. ``startup`` re-exports both functions as the
public surface.
"""

from __future__ import annotations

import os
import time as _time_module
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.protocol.env import (
    MCP_BOOT_ALLOWANCE_MS_ENV,
    MCP_PREFLIGHT_TIMEOUT_MS_ENV,
    MCP_UPSTREAM_PROBE_TIMEOUT_MS_ENV,
)
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import read_overruns, record_overrun

# Env var for the diagnostic pre-serve stage budget. Retained for diagnostic
# stage budgeting and backward compatibility; runtime enforcement uses the
# remaining real startup deadline.
_MCP_PRE_SERVE_TIMEOUT_MS_ENV = "RALPH_MCP_PRE_SERVE_TIMEOUT_MS"

# Env var for the bounded overrun diagnostic exit budget.
_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS_ENV = "RALPH_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS"

if TYPE_CHECKING:
    from collections.abc import Mapping

#: A monotonic clock: returns seconds elapsed since an arbitrary origin.
#:
#: The startup budgets are measured with an INJECTED clock so callers (and
#: tests) can drive the deadline arithmetic without patching
#: ``time.monotonic``. Production callers omit the clock and get
#: :func:`mcp_startup_now`, the module clock.
ClockFn = Callable[[], float]

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

# Interpreter boot + import of the MCP server child happens BEFORE the child
# starts its own startup clock (``t0`` in ``run_standalone_server`` is taken
# after the imports). It is therefore host-dependent time that the
# pre-serve envelope above never accounted for: under heavy swap/iowait a cold
# ``python -m ralph.mcp.server`` can take far longer than the ~1.8s of slack
# the spawn-anchored parent deadline left. The parent budgets it separately: a
# live child gets this long to record its first stage, and only then does the
# pre-serve envelope start counting.
_DEFAULT_BOOT_ALLOWANCE = timedelta(seconds=90)

_DISCOVERY_METHOD = "tools/list"

# Budget for an upstream tool call, which runs outside the startup window.
_UPSTREAM_CALL_TIMEOUT_SECONDS = 30.0

# Individual upper bounds on how long ONE startup stage may take. These are
# per-stage envelopes, not an aggregate: the aggregate spawn-to-tools/list
# bound is the discovery budget enforced by the shared deadline, NOT the sum
# of these.
_PRE_SERVE_BUDGET_S: float = 1.0
_CONFIG_LOAD_BUDGET_S: float = 1.0
_REGISTRY_BUILD_BUDGET_S: float = 0.4
_READY_TO_SERVE_BUDGET_S: float = 0.4
_OVERRUN_DIAGNOSTIC_BUDGET_S: float = 0.2

if not (_PRE_SERVE_BUDGET_S > 0):
    raise RuntimeError(f"_PRE_SERVE_BUDGET_S must be > 0, got {_PRE_SERVE_BUDGET_S!r}")
if not (_CONFIG_LOAD_BUDGET_S > 0):
    raise RuntimeError(f"_CONFIG_LOAD_BUDGET_S must be > 0, got {_CONFIG_LOAD_BUDGET_S!r}")
if not (_REGISTRY_BUILD_BUDGET_S > 0):
    raise RuntimeError(f"_REGISTRY_BUILD_BUDGET_S must be > 0, got {_REGISTRY_BUILD_BUDGET_S!r}")
if not (_READY_TO_SERVE_BUDGET_S > 0):
    raise RuntimeError(f"_READY_TO_SERVE_BUDGET_S must be > 0, got {_READY_TO_SERVE_BUDGET_S!r}")
if not (_OVERRUN_DIAGNOSTIC_BUDGET_S > 0):
    raise RuntimeError(
        f"_OVERRUN_DIAGNOSTIC_BUDGET_S must be > 0, got {_OVERRUN_DIAGNOSTIC_BUDGET_S!r}"
    )
if not (_OVERRUN_DIAGNOSTIC_BUDGET_S < _PROBE_SAFETY_S):
    raise RuntimeError(
        f"_OVERRUN_DIAGNOSTIC_BUDGET_S ({_OVERRUN_DIAGNOSTIC_BUDGET_S}s) must be "
        f"strictly less than _PROBE_SAFETY_S ({_PROBE_SAFETY_S}s) to preserve "
        f"the failure-reporting safety margin"
    )


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


def mcp_boot_allowance_from_env(env: Mapping[str, str] | None = None) -> timedelta:
    """Return how long a spawned MCP child may take to begin its own startup.

    This covers interpreter boot and module imports, i.e. the time before the
    child writes its first stage (``env_loaded``). It is additive to, and
    independent of, :func:`mcp_preflight_timeout_from_env`, which bounds the
    child's own pre-serve work. ``RALPH_MCP_BOOT_ALLOWANCE_MS`` overrides it.
    """

    env_map = os.environ if env is None else env
    return _timeout_ms_from_env(env_map, MCP_BOOT_ALLOWANCE_MS_ENV) or _DEFAULT_BOOT_ALLOWANCE


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


def mcp_pre_serve_budget_from_env(env: Mapping[str, str] | None = None) -> timedelta:
    """Return the diagnostic pre-serve stage budget (default 1.0 s, env-overridable).

    Historically, ``RALPH_MCP_PRE_SERVE_TIMEOUT_MS`` configured an individual timeout
    for the pre-serve critical path. In the current runtime, pre-serve operations
    (in :func:`ralph.mcp.server._runtime_polling.run_pre_serve_bounded`) are bounded
    operationally by the remaining real startup deadline
    (:func:`mcp_real_startup_deadline_from_env`), ensuring that slow pre-serve operations
    can use available headroom without premature termination while still failing closed
    before the preflight window expires.

    This helper and the ``RALPH_MCP_PRE_SERVE_TIMEOUT_MS`` environment variable are
    retained for diagnostic stage budgeting and backward compatibility; runtime enforcement
    uses the remaining real deadline.
    """
    env_map = os.environ if env is None else env
    requested = _timeout_ms_from_env(env_map, _MCP_PRE_SERVE_TIMEOUT_MS_ENV)
    if requested is not None:
        return requested
    return timedelta(seconds=_PRE_SERVE_BUDGET_S)


def mcp_overrun_diagnostic_timeout_from_env(
    env: Mapping[str, str] | None = None,
) -> timedelta:
    """Return the bounded fail-safe timeout for diagnostic I/O during overrun exit (default 0.2s).

    ``RALPH_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS`` overrides the budget explicitly.
    Overrun diagnostics (stage recording, logging, stdio flushing) are bounded by
    this timeout so a stalled filesystem or blocked pipe cannot hold a live unserving
    child past its deadline before ``os._exit``.
    """
    env_map = os.environ if env is None else env
    requested = _timeout_ms_from_env(env_map, _MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS_ENV)
    if requested is not None:
        return requested
    return timedelta(seconds=_OVERRUN_DIAGNOSTIC_BUDGET_S)


def mcp_overrun_diagnostic_timeout_seconds(
    env: Mapping[str, str] | None = None,
) -> float:
    """Return :func:`mcp_overrun_diagnostic_timeout_from_env` as seconds."""
    return mcp_overrun_diagnostic_timeout_from_env(env).total_seconds()


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


def mcp_real_startup_deadline_from_env(
    env: Mapping[str, str] | None = None,
) -> float:
    """Return the real cumulative startup deadline in seconds.

    This is the preflight timeout minus the probe-safety floor:
    ``(mcp_preflight_timeout_from_env(env) - timedelta(seconds=_PROBE_SAFETY_S)).total_seconds()``.
    Ensures that pre-serve operations cannot consume the failure-reporting safety margin.
    """
    env_map = os.environ if env is None else env
    return (
        mcp_preflight_timeout_from_env(env_map) - timedelta(seconds=_PROBE_SAFETY_S)
    ).total_seconds()


def mcp_probe_safety_s() -> float:
    """Return the probe-safety floor in seconds.

    The margin the child reserves to report a stalled upstream probe before
    the supervisor SIGKILLs it. The floor is subtracted from the preflight
    timeout to produce the real startup deadline
    (:func:`mcp_real_startup_deadline_from_env`).
    """

    return _PROBE_SAFETY_S


def mcp_probe_budget_share() -> float:
    """Return the share of the preflight budget one upstream probe may claim.

    The ceiling for a single upstream probe is
    ``preflight * share``; it must stay strictly above
    :func:`mcp_probe_safety_s` so the child can still report a stalled
    probe.
    """

    return _PROBE_BUDGET_SHARE


def mcp_max_upstreams_at_defaults() -> int:
    """Return the worst-case upstream count that fits the default preflight budget.

    The count is derived at import time from the default preflight budget,
    the probe share, and the probe-safety floor. It is the largest number of
    upstreams whose probes still leave the child one safety margin to fail
    loudly.
    """

    return _MAX_UPSTREAMS_AT_DEFAULTS


def mcp_stage_budgets(env: Mapping[str, str] | None = None) -> dict[str, float]:
    """Return the diagnostic per-stage budget ceilings in seconds, keyed by stage.

    These ceilings are DIAGNOSTIC ONLY: a stage that exceeds its ceiling is
    recorded as an overrun warning and the startup continues. The only fatal
    envelope is :func:`mcp_real_startup_deadline_from_env`.
    """

    return {
        "config_loading": _PRE_SERVE_BUDGET_S,
        "config_loaded": _CONFIG_LOAD_BUDGET_S,
        "upstream_catalog_or_discovery_done": mcp_discovery_budget_from_env(env).total_seconds(),
        "registry_built": _REGISTRY_BUILD_BUDGET_S,
        "ready_to_serve": _READY_TO_SERVE_BUDGET_S,
    }


_OVERRUN_LOGGER_NAME = "ralph.mcp.protocol._startup_timeouts"


def record_stage_elapsed(
    stage: str,
    elapsed_s: float,
    *,
    budget_s: float,
    root: Path | None = None,
    headroom_s: float | None = None,
) -> float | None:
    """Diagnose a per-stage startup budget overrun; return ``None`` on overrun.

    This function is DIAGNOSTIC ONLY. It logs a structured WARNING for per-stage
    overruns and records an overrun entry in the sibling JSONL file via
    :func:`record_overrun`, but never raises. Use ``_check_real_startup_deadline``
    for the only fatal deadline check.

    Fake-clock friendly by design: the caller measures ``elapsed_s`` (with its
    own clock) and passes it in; this function never reads ``time.monotonic``,
    so tests control time entirely.

    Returns:
        ``None`` when ``elapsed_s > budget_s`` (overrun diagnosed).
        ``budget_s`` when ``elapsed_s <= budget_s`` (within budget).

    Raises:
        RuntimeError: if ``budget_s`` is not strictly positive.
    """
    if not (budget_s > 0):
        raise RuntimeError(f"budget_s must be strictly positive, got {budget_s!r}")

    if elapsed_s > budget_s:
        remaining_s = budget_s - elapsed_s
        if headroom_s is not None:
            logger.warning(
                "startup stage {stage} over budget: "
                "elapsed_s={elapsed_s:.3f}s budget_s={budget_s:.3f}s remaining_s={remaining_s:.3f}s headroom_s={headroom_s:.3f}s",
                stage=stage,
                elapsed_s=elapsed_s,
                budget_s=budget_s,
                remaining_s=remaining_s,
                headroom_s=headroom_s,
            )
        else:
            logger.warning(
                "startup stage {stage} over budget: "
                "elapsed_s={elapsed_s:.3f}s budget_s={budget_s:.3f}s remaining_s={remaining_s:.3f}s",
                stage=stage,
                elapsed_s=elapsed_s,
                budget_s=budget_s,
                remaining_s=remaining_s,
            )
        target_root = root if root is not None else Path.cwd()
        try:
            record_overrun(
                target_root,
                stage,
                elapsed_s=elapsed_s,
                budget_s=budget_s,
                remaining_s=remaining_s,
                headroom_s=headroom_s,
                timeout_s=mcp_overrun_diagnostic_timeout_seconds(),
            )
        except Exception as exc:
            logger.warning("failed to record overrun to stage tracker: {exc}", exc=exc)
        return None

    return budget_s


def mcp_startup_now() -> float:
    """Return the current startup monotonic timestamp in seconds.

    Single source of truth for the clock the startup deadlines are measured
    against. Production call sites omit the ``clock_fn`` argument of
    :func:`check_real_startup_deadline`, which makes the deadline helpers use
    this clock; callers that need a deterministic timeline (tests, replayed
    diagnostics) inject their own clock instead of patching
    ``time.monotonic``.
    """

    return _time_module.monotonic()


def check_real_startup_deadline(
    stage: str,
    t0: float,
    env_map: Mapping[str, str] | None = None,
    *,
    clock_fn: ClockFn | None = None,
) -> None:
    """Enforce the preflight startup deadline at a stage boundary.

    Checks elapsed monotonic time from ``t0`` against the real startup envelope:
    ``mcp_real_startup_deadline_from_env(env_map)``.
    Default: 30.0s - 2.0s = 28.0s.

    This is the ONLY fatal startup check. Per-stage budget overruns are
    diagnostic warnings only (see :func:`record_stage_elapsed`), recorded in
    the sibling JSONL ``.overruns`` file without aborting startup.

    Args:
        stage: Stage label used in the error message and ``last_stage``.
        t0: Monotonic timestamp the startup window is measured from.
        env_map: Environment map the envelope is derived from. ``None`` means
            the process environment.
        clock_fn: Injectable monotonic clock. ``None`` (the production
            default) uses the module clock :func:`mcp_startup_now`.

    Raises:
        McpServerError: when cumulative elapsed exceeds the real startup deadline.
    """

    clk = clock_fn if clock_fn is not None else mcp_startup_now
    elapsed = clk() - t0
    real_deadline_s = mcp_real_startup_deadline_from_env(env_map)
    if elapsed > real_deadline_s:
        raise McpServerError(
            f"real startup deadline exceeded at stage {stage!r} "
            f"({elapsed:.3f}s > {real_deadline_s:.3f}s)",
            last_stage=stage,
            elapsed_s=elapsed,
            restart_count=0,
        )


def _check_real_startup_deadline(
    stage: str,
    t0: float,
    env_map: Mapping[str, str] | None = None,
) -> None:
    """Thin backward-compatibility wrapper delegating to :func:`check_real_startup_deadline`."""
    check_real_startup_deadline(stage, t0, env_map)


def _check_cumulative_startup_deadline(
    stage: str,
    t0: float,
    env_map: Mapping[str, str] | None = None,
) -> None:
    """Thin backward-compatibility wrapper delegating to :func:`check_real_startup_deadline`."""
    check_real_startup_deadline(stage, t0, env_map)


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

# Import-time invariant: the real startup deadline (preflight - probe-safety) must
# be strictly positive and strictly less than the preflight timeout.
_real_startup_deadline_s: float = mcp_preflight_timeout_from_env().total_seconds() - _PROBE_SAFETY_S
if not (0 < _real_startup_deadline_s < mcp_preflight_timeout_from_env().total_seconds()):
    raise RuntimeError(
        f"real startup deadline ({_real_startup_deadline_s}s) must be strictly "
        f"positive and strictly less than preflight "
        f"({mcp_preflight_timeout_from_env().total_seconds()}s)"
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


def mcp_registry_read_timeout_from_env(
    env: Mapping[str, str] | None = None,
    *,
    remaining_s: float | None = None,
) -> float:
    """Return the bounded per-workspace-read timeout for registry construction."""
    env_map = os.environ if env is None else env
    requested = _timeout_ms_from_env(env_map, "RALPH_MCP_REGISTRY_READ_TIMEOUT_MS")
    if requested is not None:
        timeout = requested.total_seconds()
        if remaining_s is not None:
            return min(timeout, max(0.0, remaining_s))
        return timeout
    if remaining_s is not None:
        return max(0.0, remaining_s)
    return mcp_real_startup_deadline_from_env(env_map)


def mcp_whole_startup_budget_from_env(
    env: Mapping[str, str] | None = None,
) -> float:
    """Return the whole startup envelope budget ceiling across all startup stages."""
    return mcp_registry_built_budget_from_env(env) + _READY_TO_SERVE_BUDGET_S


_CONFIG_LOAD_CUMULATIVE_S: float = _CONFIG_LOAD_BUDGET_S
_REGISTRY_BUILT_CUMULATIVE_S: float = mcp_registry_built_budget_from_env()
_WHOLE_STARTUP_BUDGET_S: float = mcp_whole_startup_budget_from_env()
_READY_TO_SERVE_CUMULATIVE_S: float = _WHOLE_STARTUP_BUDGET_S

if not (0 < _CONFIG_LOAD_CUMULATIVE_S < _REGISTRY_BUILT_CUMULATIVE_S < _WHOLE_STARTUP_BUDGET_S):
    raise RuntimeError(
        f"import-time invariant violated: cumulative budgets must be strictly monotonic: "
        f"{_CONFIG_LOAD_CUMULATIVE_S} < {_REGISTRY_BUILT_CUMULATIVE_S} < {_WHOLE_STARTUP_BUDGET_S}"
    )

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
    "_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS_ENV",
    "_OVERRUN_DIAGNOSTIC_BUDGET_S",
    "_PRE_SERVE_BUDGET_S",
    "_PROBE_SAFETY_S",
    "_READY_TO_SERVE_BUDGET_S",
    "_READY_TO_SERVE_CUMULATIVE_S",
    "_REGISTRY_BUILD_BUDGET_S",
    "_REGISTRY_BUILT_CUMULATIVE_S",
    "_WHOLE_STARTUP_BUDGET_S",
    "ClockFn",
    "_check_cumulative_startup_deadline",
    "_check_real_startup_deadline",
    "check_real_startup_deadline",
    "mcp_boot_allowance_from_env",
    "mcp_discovery_budget_from_env",
    "mcp_max_upstreams_at_defaults",
    "mcp_overrun_diagnostic_timeout_from_env",
    "mcp_overrun_diagnostic_timeout_seconds",
    "mcp_pre_serve_budget_from_env",
    "mcp_preflight_timeout_from_env",
    "mcp_probe_budget_share",
    "mcp_probe_safety_s",
    "mcp_real_startup_deadline_from_env",
    "mcp_registry_built_budget_from_env",
    "mcp_registry_read_timeout_from_env",
    "mcp_stage_budgets",
    "mcp_startup_now",
    "mcp_upstream_probe_timeout_from_env",
    "mcp_upstream_probe_timeout_seconds",
    "mcp_whole_startup_budget_from_env",
    "read_overruns",
    "record_overrun",
    "record_stage_elapsed",
    "upstream_call_timeout_seconds",
]
