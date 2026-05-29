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


def verify_process_liveness(
    pid: int,
    *,
    psutil_mod: _PsutilModuleLike | None = None,
) -> LivenessResult:
    """Check whether a PID is still alive, gone, zombie, or unknown.

    On POSIX, uses os.kill(pid, 0) to probe process existence.
    On Windows, falls back to psutil.pid_exists() if available.
    Uses psutil for zombie detection when available.

    Does NOT use _SuppressMissingProcess — each exception type is handled
    explicitly to avoid conflating "process gone" with "no permission".

    Args:
        pid: Process ID to check.
        psutil_mod: Optional psutil module-like object for extended checks.

    Returns:
        LivenessResult indicating the process state.
    """
    if psutil_mod is not None:
        try:
            proc = psutil_mod.process_from_pid(pid)
            status = proc.status()
            if status == "zombie":
                return LivenessResult.ZOMBIE
        except Exception:
            pass

    if hasattr(os, "kill"):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return LivenessResult.GONE
        except PermissionError:
            return LivenessResult.ALIVE
        except OSError:
            return LivenessResult.UNKNOWN
        else:
            return LivenessResult.ALIVE

    if psutil_mod is not None:
        try:
            exists = psutil_mod.pid_exists(pid)
            if exists:
                return LivenessResult.ALIVE
            return LivenessResult.GONE
        except Exception:
            return LivenessResult.UNKNOWN

    return LivenessResult.UNKNOWN
