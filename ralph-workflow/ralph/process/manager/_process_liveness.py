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


def _is_gone_error(psutil_mod: _PsutilModuleLike, exc: BaseException) -> bool:
    if isinstance(exc, ProcessLookupError):
        return True
    try:
        nsp = psutil_mod.NoSuchProcess
        return isinstance(exc, nsp)
    except (AttributeError, TypeError):
        return False


def _is_access_denied_error(psutil_mod: _PsutilModuleLike, exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    try:
        denied = psutil_mod.AccessDenied
        return isinstance(exc, denied)
    except (AttributeError, TypeError):
        return False


def _psutil_pid_exists_liveness(psutil_mod: _PsutilModuleLike, pid: int) -> LivenessResult:
    try:
        exists = psutil_mod.pid_exists(pid)
    except BaseException as exc:
        if _is_gone_error(psutil_mod, exc):
            return LivenessResult.GONE
        if _is_access_denied_error(psutil_mod, exc):
            return LivenessResult.ALIVE
        match exc:
            case OSError() | RuntimeError() | TypeError() | ValueError():
                return LivenessResult.UNKNOWN
            case _:
                raise
    else:
        return LivenessResult.ALIVE if exists else LivenessResult.GONE


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
        except BaseException as exc:
            if _is_gone_error(psutil_mod, exc):
                return LivenessResult.GONE
            if _is_access_denied_error(psutil_mod, exc):
                return LivenessResult.ALIVE
            match exc:
                case OSError() | RuntimeError() | TypeError() | ValueError():
                    pass
                case _:
                    raise
        else:
            return _psutil_pid_exists_liveness(psutil_mod, pid)

    if hasattr(os, "kill"):
        return _posix_liveness(pid)
    return LivenessResult.UNKNOWN
