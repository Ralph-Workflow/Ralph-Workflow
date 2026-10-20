"""Real host-contention signal producers.

Each producer is exception-safe, thread-safe, and cheap.  They all
delegate to ``record_host_contention_signal`` from the sibling module
``_mcp_health_event``.
"""

from __future__ import annotations

import time
from collections.abc import Callable  # used at runtime in function signatures
from datetime import timedelta
from pathlib import Path

try:
    import psutil
except Exception:  # pragma: no cover — psutil unavailable in some envs
    psutil = None  # type: ignore[assignment]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

#: PSI avg10 threshold (percent) above which a pressure signal is emitted.
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
    """Emit a contention signal if the given process is in a D-state.

    D-states are kernel-mode waits (uninterruptible sleep), almost always
    I/O blockers.  Emits ``d_state:<status>`` where ``<status>`` is the
    exact psutil status string (e.g. ``disk-sleep`` or ``D``).

    ``_now`` is a private test seam.
    """
    if psutil is not None:
        try:
            status = psutil.Process(pid).status()
            if status.startswith("D") or status == "disk-sleep":
                from ralph.mcp.server._mcp_health_event import (  # noqa: PLC0415
                    record_host_contention_signal,
                )

                record_host_contention_signal(f"d_state:{status}", _now=_now)
        except Exception:
            # psutil errors are common in production (process disappeared, permission
            # denied, etc.).  Never let them propagate.
            pass


def record_sigkill_survivor(
    pid: int,
    *,
    _now: Callable[[], float] | None = None,
) -> None:
    """Emit a contention signal if the given process survived SIGKILL.

    A process that was sent SIGKILL and is still present is either a
    zombie (already reaped by its parent) or stuck in disk-sleep.  Both
    indicate host-level resource contention or a runaway process tree.
    Emits ``sigkill_survivor:<status>``.

    ``_now`` is a private test seam.
    """
    if psutil is not None:
        try:
            status = psutil.Process(pid).status()
            if status in ("disk-sleep", "zombie"):
                from ralph.mcp.server._mcp_health_event import (  # noqa: PLC0415
                    record_host_contention_signal,
                )

                record_host_contention_signal(f"sigkill_survivor:{status}", _now=_now)
        except Exception:
            # Process may have been reaped between the kill attempt and this
            # check; that race is benign.
            pass


def record_psi_pressure(
    *,
    _now: Callable[[], float] | None = None,
) -> None:
    """Emit a contention signal when any PSI metric exceeds 50 % avg10.

    Reads ``/proc/pressure/cpu``, ``/proc/pressure/io``, and
    ``/proc/pressure/memory``.  For each file that exists, parses the
    ``some`` line and emits ``psi_pressure:<type>:<avg10>`` when avg10
    is at or above 50.0.

    Missing or unreadable PSI files are silently skipped.
    ``_now`` is a private test seam (not used internally but accepted for
    API consistency).
    """
    psi_types = ("cpu", "io", "memory")
    for name in psi_types:
        try:
            path = Path(f"/proc/pressure/{name}")
            # filesystem-read-ok: /proc/pressure/* is a virtual kernel file that exists only when the host exposes PSI; the read either succeeds and yields the kernel-formatted ``some`` line or fails (ENOENT) and the producer silently skips. There is no FileBackend abstraction over virtual kernel files; routing through one would silently swallow the kernel's ENOENT signal and turn PSI instrumentation into a no-op.
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("some "):
                        # Format: "some avg10=1.23 avg60=4.56 avg300=7.89"
                        parts = line.split()
                        for part in parts:
                            if part.startswith("avg10="):
                                avg10_str = part.split("=", 1)[1]
                                avg10 = float(avg10_str)
                                if avg10 >= PSI_AVG10_THRESHOLD:
                                    from ralph.mcp.server._mcp_health_event import (  # noqa: PLC0415
                                        record_host_contention_signal,
                                    )

                                    record_host_contention_signal(
                                        f"psi_pressure:{name}:{avg10}", _now=_now
                                    )
                        break  # only the "some" line matters
        except Exception:
            # PSI files may be absent (non-Linux) or temporarily unreadable.
            pass


def record_supervisor_tick_lateness(
    expected_at: float,
    *,
    check_interval: timedelta = timedelta(seconds=5),
    _now: Callable[[], float] | None = None,
) -> None:
    """Emit a contention signal when the supervisor tick is more than 2x late.

    If the gap between ``expected_at`` (a ``time.monotonic()`` value) and
    the current monotonic clock exceeds ``check_interval.total_seconds() *
    2.0``, a ``supervisor_tick_lateness`` signal is emitted.

    ``_now`` replaces ``time.monotonic`` for testing.
    """
    now_fn: Callable[[], float] = _now if _now is not None else time.monotonic
    if now_fn() - expected_at > check_interval.total_seconds() * 2.0:
        from ralph.mcp.server._mcp_health_event import (  # noqa: PLC0415
            record_host_contention_signal,
        )

        record_host_contention_signal("supervisor_tick_lateness", _now=_now)
