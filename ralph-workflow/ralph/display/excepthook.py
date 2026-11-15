from __future__ import annotations

import contextlib
import sys
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable
    from types import TracebackType

    MainThreadExceptionHook = Callable[
        [type[BaseException], BaseException, TracebackType | None], None
    ]
    WorkerThreadExceptionHook = Callable[[threading.ExceptHookArgs], None]

from ralph.display.line_sanitizer import strip_terminal_control
from ralph.display.terminal_restore import restore_terminal


@dataclass(frozen=True, slots=True)
class MainThreadExceptionBoundary:
    stderr: TextIO
    restore: Callable[[], None]

    def write_exception(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException | None,
        exc_traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_value is not None:
                for line in traceback.format_exception(exc_type, exc_value, exc_traceback):
                    self.stderr.write(strip_terminal_control(line))
            self.stderr.flush()
        finally:
            with contextlib.suppress(Exception):
                self.restore()

    def handle_main_thread_exception(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        with contextlib.suppress(Exception):
            self.write_exception(exc_type, exc_value, exc_traceback)

    def handle_worker_thread_exception(self, args: threading.ExceptHookArgs) -> None:
        with contextlib.suppress(Exception):
            self.write_exception(args.exc_type, args.exc_value, args.exc_traceback)


def install_sanitizing_excepthook(
    *,
    stderr: TextIO | None = None,
    sys_setter: Callable[[MainThreadExceptionHook], object] | None = None,
    thread_setter: Callable[[WorkerThreadExceptionHook], object] | None = None,
    restore: Callable[[], None] | None = None,
) -> MainThreadExceptionBoundary:
    boundary = MainThreadExceptionBoundary(
        stderr=stderr if stderr is not None else sys.stderr,
        restore=restore if restore is not None else restore_terminal,
    )
    if sys_setter is not None:
        with contextlib.suppress(Exception):
            sys_setter(boundary.handle_main_thread_exception)
    if thread_setter is not None:
        with contextlib.suppress(Exception):
            thread_setter(boundary.handle_worker_thread_exception)
    return boundary


__all__ = ["MainThreadExceptionBoundary", "install_sanitizing_excepthook"]
