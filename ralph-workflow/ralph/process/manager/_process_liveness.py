"""Process liveness detection utilities."""

from __future__ import annotations

import enum
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.process.manager._process_manager_types import _PsutilModuleLike


class LivenessResult(enum.Enum):
    """Result of a process liveness check."""

    ALIVE = "alive"
    GONE = "gone"
    ZOMBIE = "zombie"
    UNKNOWN = "unknown"


def _posix_liveness(pid: int) -> LivenessResult:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return LivenessResult.GONE
    except PermissionError:
        return LivenessResult.ALIVE
    except OSError:
        return LivenessResult.UNKNOWN
    return LivenessResult.ALIVE


def _psutil_pid_exists_liveness(psutil_mod: _PsutilModuleLike, pid: int) -> LivenessResult:
    try:
        exists = psutil_mod.pid_exists(pid)
        return LivenessResult.ALIVE if exists else LivenessResult.GONE
    except Exception:
        return LivenessResult.UNKNOWN


def verify_process_liveness(
    pid: int,
    *,
    psutil_mod: _PsutilModuleLike | None = None,
) -> LivenessResult:
    """Check whether a PID is still alive, gone, zombie, or unknown.

    When psutil_mod is available, uses it as the primary liveness check
    (pid_exists + zombie detection). Falls back to os.kill(pid, 0) on
    POSIX only when psutil_mod is None.

    Does NOT use _SuppressMissingProcess — each exception type is handled
    explicitly to avoid conflating "process gone" with "no permission".

    Args:
        pid: Process ID to check.
        psutil_mod: Optional psutil module-like object for extended checks.

    Returns:
        LivenessResult indicating the process state.
    """
    if psutil_mod is not None:
        # Zombie detection via psutil
        try:
            proc = psutil_mod.process_from_pid(pid)
            if proc.status() == "zombie":
                return LivenessResult.ZOMBIE
        except Exception:
            # process_from_pid failed — fall through to OS-level check
            pass
        else:
            # process_from_pid succeeded — use psutil for primary liveness check
            return _psutil_pid_exists_liveness(psutil_mod, pid)

    # No psutil — fall back to OS-level check
    if hasattr(os, "kill"):
        return _posix_liveness(pid)
    return LivenessResult.UNKNOWN
