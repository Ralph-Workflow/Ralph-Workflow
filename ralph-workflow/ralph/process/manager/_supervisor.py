
from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

_PR_SET_PDEATHSIG: Final[int] = 1
_SIGTERM: Final[int] = 15
_SIGKILL: Final[int] = 9
_GROUP_TERM_GRACE_S: Final[float] = 5.0
_MALLOC_DEBUG_NOISE_VARS: Final[tuple[str, ...]] = (
    "MallocStackLogging",
    "MallocStackLoggingNoCompact",
)


def sanitize_process_environment() -> tuple[str, ...]:
    return tuple(
        name for name in _MALLOC_DEBUG_NOISE_VARS if os.environ.pop(name, None) is not None
    )


class _Prctl(Protocol):

    def __call__(self, option: int, value: int) -> int: ...


_libc = ctypes.CDLL(None, use_errno=True)
_prctl_raw: object = getattr(_libc, "prctl", None)
if callable(_prctl_raw):
    _prctl: _Prctl = cast(
        "_Prctl", _prctl_raw
    )
else:

    def _prctl(option: int, value: int) -> int:
        del option, value
        return -1


def _set_pdeathsig(signal_number: int) -> bool:
    parent_before = os.getppid()
    if parent_before <= 1:
        return False
    rc = _prctl(_PR_SET_PDEATHSIG, signal_number)
    if rc != 0:
        return False
    return os.getppid() == parent_before


def _other_group_members_alive(pgid: int) -> bool:
    me = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            stat_text = (entry / "stat").read_text(encoding="utf-8")
        except OSError:
            continue
        fields = stat_text.rsplit(")", 1)[1].split()
        if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
            return True
    return False


def _kill_tree_and_exit() -> NoReturn:
    pgid = os.getpid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pgid, _SIGTERM)
        deadline = time.monotonic() + _GROUP_TERM_GRACE_S
        while time.monotonic() < deadline and _other_group_members_alive(pgid):
            time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pgid, _SIGKILL)
    os._exit(0)


def _forward_to_group(signum: int) -> None:
    handler = signal.getsignal(signum)
    signal.signal(signum, signal.SIG_IGN)
    try:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpid(), signum)
    finally:
        signal.signal(signum, handler)


def _termination_handler(holder_pid: int) -> Callable[[int, FrameType | None], None]:

    def handle(signum: int, frame: FrameType | None) -> None:
        del frame
        if os.getppid() != holder_pid:
            _kill_tree_and_exit()
        _forward_to_group(signum)

    return handle


def run(argv: list[str]) -> int:
    sanitize_process_environment()
    if not argv:
        return 127

    on_termination = _termination_handler(os.getppid())
    signal.signal(signal.SIGTERM, on_termination)
    signal.signal(signal.SIGINT, on_termination)
    signal.signal(signal.SIGHUP, on_termination)

    if not _set_pdeathsig(_SIGTERM):
        return 126

    pid = os.fork()
    if pid == 0:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        _set_pdeathsig(_SIGKILL)
        with contextlib.suppress(OSError):
            os.execvp(argv[0], argv)
        return 127

    while True:
        try:
            _, status = os.waitpid(pid, 0)
        except OSError:
            return 1
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return 128 + os.WTERMSIG(status)


def main() -> int:
    sanitize_process_environment()
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
