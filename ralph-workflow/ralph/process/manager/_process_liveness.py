
from __future__ import annotations

import enum
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.process.manager._process_manager_types import _PsutilModuleLike


class LivenessResult(enum.Enum):

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
    if psutil_mod is not None:
        try:
            proc = psutil_mod.process_from_pid(pid)
            if proc.status() == "zombie":
                return LivenessResult.ZOMBIE
        except Exception:
            pass
        else:
            return _psutil_pid_exists_liveness(psutil_mod, pid)

    if hasattr(os, "kill"):
        return _posix_liveness(pid)
    return LivenessResult.UNKNOWN
