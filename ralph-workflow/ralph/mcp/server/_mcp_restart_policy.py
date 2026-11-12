from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class McpRestartPolicy:
    max_restarts: int = 20
    cooldown_seconds: float = 2.0
    host_contention_window_seconds: float = 10.0
    max_contention_deferrals: int = 3
    rolling_max_in_window: int = 6
    rolling_window_s: float = 600.0
    healthy_reset_s: float = 300.0


_HOST_CONTENTION_WINDOW_MIN_S: float = 6.0
_MAX_CONTENTION_DEFERRALS_MIN: int = 1
_MAX_CONTENTION_DEFERRALS_MAX: int = 10
_ROLLING_MAX_IN_WINDOW_MIN: int = 1

_DEFAULT_COOLDOWN_SECONDS: float = McpRestartPolicy.cooldown_seconds
_DEFAULT_MAX_RESTARTS: int = McpRestartPolicy.max_restarts
if not _DEFAULT_COOLDOWN_SECONDS > 0:
    raise RuntimeError(
        f"McpRestartPolicy.cooldown_seconds default must be positive "
        f"(got {_DEFAULT_COOLDOWN_SECONDS})"
    )
if not _DEFAULT_MAX_RESTARTS > 0:
    raise RuntimeError(
        f"McpRestartPolicy.max_restarts default must be positive (got {_DEFAULT_MAX_RESTARTS})"
    )
_DEFAULT_HOST_CONTENTION_WINDOW_S: float = McpRestartPolicy.host_contention_window_seconds
_DEFAULT_MAX_CONTENTION_DEFERRALS: int = McpRestartPolicy.max_contention_deferrals
if not _DEFAULT_HOST_CONTENTION_WINDOW_S > _HOST_CONTENTION_WINDOW_MIN_S:
    raise RuntimeError(
        f"McpRestartPolicy.host_contention_window_seconds default must be > "
        f"{_HOST_CONTENTION_WINDOW_MIN_S}s (got {_DEFAULT_HOST_CONTENTION_WINDOW_S})"
    )
if not (
    _MAX_CONTENTION_DEFERRALS_MIN
    <= _DEFAULT_MAX_CONTENTION_DEFERRALS
    <= _MAX_CONTENTION_DEFERRALS_MAX
):
    raise RuntimeError(
        f"McpRestartPolicy.max_contention_deferrals default must be in "
        f"[{_MAX_CONTENTION_DEFERRALS_MIN}, {_MAX_CONTENTION_DEFERRALS_MAX}] "
        f"(got {_DEFAULT_MAX_CONTENTION_DEFERRALS})"
    )
_DEFAULT_ROLLING_MAX_IN_WINDOW: int = McpRestartPolicy.rolling_max_in_window
if not _DEFAULT_ROLLING_MAX_IN_WINDOW > 0:
    raise RuntimeError(
        f"McpRestartPolicy.rolling_max_in_window default must be positive "
        f"(got {_DEFAULT_ROLLING_MAX_IN_WINDOW})"
    )
_DEFAULT_ROLLING_WINDOW_S: float = McpRestartPolicy.rolling_window_s
_DEFAULT_HEALTHY_RESET_S: float = McpRestartPolicy.healthy_reset_s
if not _DEFAULT_ROLLING_WINDOW_S > 0:
    raise RuntimeError(
        f"McpRestartPolicy.rolling_window_s default must be positive "
        f"(got {_DEFAULT_ROLLING_WINDOW_S})"
    )
if not _DEFAULT_HEALTHY_RESET_S > 0:
    raise RuntimeError(
        f"McpRestartPolicy.healthy_reset_s default must be positive "
        f"(got {_DEFAULT_HEALTHY_RESET_S})"
    )
if not _DEFAULT_ROLLING_WINDOW_S > _DEFAULT_HEALTHY_RESET_S:
    raise RuntimeError(
        f"McpRestartPolicy.rolling_window_s default must be > healthy_reset_s "
        f"(got {_DEFAULT_ROLLING_WINDOW_S} <= {_DEFAULT_HEALTHY_RESET_S})"
    )


def next_budget_decision(
    policy: McpRestartPolicy,
    current_count: int,
    rolling_history: tuple[float, ...],
    rolling_window_s: float,
    *,
    _now: Callable[[], float] | None = None,
) -> str:
    now_fn: Callable[[], float] = _now if _now is not None else time.monotonic
    now = now_fn()
    raw_max: object = getattr(policy, "max_restarts", 20)
    max_restarts: int = int(raw_max) if isinstance(raw_max, (int, float)) else 20
    if current_count >= max_restarts:
        return "absolute_cap"
    raw_rolling: object = getattr(policy, "rolling_max_in_window", None)
    if isinstance(raw_rolling, (int, float)):
        rolling_count = sum(1 for ts in rolling_history if now - ts <= rolling_window_s)
        if rolling_count >= raw_rolling:
            return "window_drain"
    return "ok"


__all__ = ["McpRestartPolicy", "next_budget_decision"]
