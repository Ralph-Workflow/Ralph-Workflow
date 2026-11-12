from __future__ import annotations

import os
import time as _time_module
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
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

_MCP_PRE_SERVE_TIMEOUT_MS_ENV = "RALPH_MCP_PRE_SERVE_TIMEOUT_MS"

_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS_ENV = "RALPH_MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS"

if TYPE_CHECKING:
    from collections.abc import Mapping

ClockFn = Callable[[], float]

_DEFAULT_PREFLIGHT = timedelta(milliseconds=30_000)

_PROBE_BUDGET_SHARE = 0.5

_PROBE_SAFETY_S: float = 2.0

_MAX_UPSTREAMS_AT_DEFAULTS: int = int(
    (_DEFAULT_PREFLIGHT * _PROBE_BUDGET_SHARE - timedelta(seconds=_PROBE_SAFETY_S))
    / timedelta(seconds=_PROBE_SAFETY_S)
)

if not (_PROBE_SAFETY_S > 0):
    raise RuntimeError(f"_PROBE_SAFETY_S must be > 0, got {_PROBE_SAFETY_S!r}")
if _MAX_UPSTREAMS_AT_DEFAULTS < 1:
    raise RuntimeError(
        f"_MAX_UPSTREAMS_AT_DEFAULTS must be >= 1, got {_MAX_UPSTREAMS_AT_DEFAULTS!r}"
    )

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

_DEFAULT_BOOT_ALLOWANCE = timedelta(seconds=90)

_DISCOVERY_METHOD = "tools/list"

_UPSTREAM_CALL_TIMEOUT_SECONDS = 30.0

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

    env_map = os.environ if env is None else env
    return _timeout_ms_from_env(env_map, MCP_BOOT_ALLOWANCE_MS_ENV) or _DEFAULT_BOOT_ALLOWANCE


def mcp_upstream_probe_timeout_from_env(env: Mapping[str, str] | None = None) -> timedelta:

    env_map = os.environ if env is None else env
    ceiling = mcp_preflight_timeout_from_env(env_map) * _PROBE_BUDGET_SHARE
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

    return mcp_upstream_probe_timeout_from_env(env).total_seconds()


def mcp_pre_serve_budget_from_env(env: Mapping[str, str] | None = None) -> timedelta:
    env_map = os.environ if env is None else env
    requested = _timeout_ms_from_env(env_map, _MCP_PRE_SERVE_TIMEOUT_MS_ENV)
    if requested is not None:
        return requested
    return timedelta(seconds=_PRE_SERVE_BUDGET_S)


def mcp_pre_serve_timeout_seconds(
    env: Mapping[str, str] | None = None,
    *,
    t0: float | None = None,
    clock_fn: ClockFn | None = None,
) -> float:
    clk = clock_fn if clock_fn is not None else mcp_startup_now
    eff_t0 = t0 if t0 is not None else clk()
    real_deadline_s = mcp_real_startup_deadline_from_env(env)
    return max(0.0, real_deadline_s - (clk() - eff_t0))


def mcp_pre_serve_timeout_from_env(
    env: Mapping[str, str] | None = None,
    *,
    t0: float | None = None,
    clock_fn: ClockFn | None = None,
) -> float:
    return mcp_pre_serve_timeout_seconds(env, t0=t0, clock_fn=clock_fn)


def mcp_overrun_diagnostic_timeout_from_env(
    env: Mapping[str, str] | None = None,
) -> timedelta:
    env_map = os.environ if env is None else env
    requested = _timeout_ms_from_env(env_map, _MCP_OVERRUN_DIAGNOSTIC_TIMEOUT_MS_ENV)
    if requested is not None:
        return requested
    return timedelta(seconds=_OVERRUN_DIAGNOSTIC_BUDGET_S)


def mcp_overrun_diagnostic_timeout_seconds(
    env: Mapping[str, str] | None = None,
) -> float:
    return mcp_overrun_diagnostic_timeout_from_env(env).total_seconds()


def mcp_discovery_budget_from_env(env: Mapping[str, str] | None = None) -> timedelta:

    preflight = mcp_preflight_timeout_from_env(env)
    return preflight - timedelta(seconds=_PROBE_SAFETY_S)


def mcp_real_startup_deadline_from_env(
    env: Mapping[str, str] | None = None,
) -> float:
    env_map = os.environ if env is None else env
    return (
        mcp_preflight_timeout_from_env(env_map) - timedelta(seconds=_PROBE_SAFETY_S)
    ).total_seconds()


def mcp_probe_safety_s() -> float:

    return _PROBE_SAFETY_S


def mcp_probe_budget_share() -> float:

    return _PROBE_BUDGET_SHARE


def mcp_max_upstreams_at_defaults() -> int:

    return _MAX_UPSTREAMS_AT_DEFAULTS


def mcp_stage_budgets(env: Mapping[str, str] | None = None) -> dict[str, float]:

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


_startup_clock: ContextVar[ClockFn | None] = ContextVar("ralph_mcp_startup_clock", default=None)


def set_startup_clock(clock: ClockFn | None) -> Token[ClockFn | None]:
    return _startup_clock.set(clock)


def get_startup_clock() -> ClockFn | None:
    return _startup_clock.get()


def reset_startup_clock(token: Token[ClockFn | None]) -> None:
    _startup_clock.reset(token)


@contextmanager
def startup_clock_override(clock: ClockFn | None) -> Iterator[None]:
    token = set_startup_clock(clock)
    try:
        yield
    finally:
        reset_startup_clock(token)


def mcp_startup_now() -> float:
    clk = get_startup_clock()
    if clk is not None:
        return clk()
    return _time_module.monotonic()


def check_real_startup_deadline(
    stage: str,
    t0: float,
    env_map: Mapping[str, str] | None = None,
    *,
    clock_fn: ClockFn | None = None,
) -> None:

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
    check_real_startup_deadline(stage, t0, env_map)


def _check_cumulative_startup_deadline(
    stage: str,
    t0: float,
    env_map: Mapping[str, str] | None = None,
) -> None:
    check_real_startup_deadline(stage, t0, env_map)


def upstream_call_timeout_seconds(method: str, env: Mapping[str, str] | None = None) -> float:

    if method == _DISCOVERY_METHOD:
        return mcp_upstream_probe_timeout_seconds(env)
    return _UPSTREAM_CALL_TIMEOUT_SECONDS


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


if not (mcp_discovery_budget_from_env() < mcp_preflight_timeout_from_env()):
    raise RuntimeError(
        "import-time invariant violated: mcp_discovery_budget_from_env() "
        "must be strictly less than mcp_preflight_timeout_from_env()"
    )

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
    "get_startup_clock",
    "mcp_boot_allowance_from_env",
    "mcp_discovery_budget_from_env",
    "mcp_max_upstreams_at_defaults",
    "mcp_overrun_diagnostic_timeout_from_env",
    "mcp_overrun_diagnostic_timeout_seconds",
    "mcp_pre_serve_budget_from_env",
    "mcp_pre_serve_timeout_from_env",
    "mcp_pre_serve_timeout_seconds",
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
    "reset_startup_clock",
    "set_startup_clock",
    "startup_clock_override",
    "upstream_call_timeout_seconds",
]
