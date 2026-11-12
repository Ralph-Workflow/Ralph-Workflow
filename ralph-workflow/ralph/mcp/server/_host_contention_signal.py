from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.server._mcp_health_event import record_host_contention_signal

if TYPE_CHECKING:
    from ralph.process.manager._process_manager_types import _PsutilModuleLike

psutil: _PsutilModuleLike | None
try:
    import psutil as _psutil_imported

    psutil = cast("_PsutilModuleLike", _psutil_imported)
except Exception:
    psutil = None

PSI_AVG10_THRESHOLD = 50.0

__all__ = [
    "record_d_state",
    "record_psi_pressure",
    "record_sigkill_survivor",
    "record_supervisor_tick_lateness",
]


def record_d_state(
    pid: int,
    *,
    _now: Callable[[], float] | None = None,
) -> None:
    if psutil is not None:
        try:
            status = psutil.Process(pid).status()
            if status.startswith("D") or status == "disk-sleep":
                record_host_contention_signal(f"d_state:{status}", _now=_now)
        except Exception:
            pass


def record_sigkill_survivor(
    pid: int,
    *,
    _now: Callable[[], float] | None = None,
) -> None:
    if psutil is not None:
        try:
            status = psutil.Process(pid).status()
            if status in ("disk-sleep", "zombie"):
                record_host_contention_signal(f"sigkill_survivor:{status}", _now=_now)
        except Exception:
            pass


def record_psi_pressure(
    *,
    _now: Callable[[], float] | None = None,
) -> None:
    psi_types = ("cpu", "io", "memory")
    for name in psi_types:
        try:
            path = Path(f"/proc/pressure/{name}")
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("some "):
                        parts = line.split()
                        for part in parts:
                            if part.startswith("avg10="):
                                avg10_str = part.split("=", 1)[1]
                                avg10 = float(avg10_str)
                                if avg10 >= PSI_AVG10_THRESHOLD:
                                    record_host_contention_signal(
                                        f"psi_pressure:{name}:{avg10}", _now=_now
                                    )
                        break
        except Exception:
            pass


def record_supervisor_tick_lateness(
    expected_at: float,
    *,
    check_interval: timedelta = timedelta(seconds=5),
    _now: Callable[[], float] | None = None,
) -> None:
    now_fn: Callable[[], float] = _now if _now is not None else time.monotonic
    if now_fn() - expected_at > check_interval.total_seconds() * 2.0:
        record_host_contention_signal("supervisor_tick_lateness", _now=_now)
