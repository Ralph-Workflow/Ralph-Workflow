"""Sanitize uncaught exception output and restore the terminal.

This boundary is intentionally output-only: it preserves exception type, message,
and traceback text while removing terminal-control bytes before they can reach
the user's terminal.  Both interpreter and worker-thread hooks are installed.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import traceback
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

from ralph.display.line_sanitizer import strip_terminal_control
from ralph.display.terminal_restore import restore_terminal


class _State:
    installed: bool = False


_STATE = _State()


def install_sanitizing_excepthook(
    *,
    stderr: TextIO | None = None,
    sys_setter: Callable[[Callable[[type[BaseException], BaseException, TracebackType | None], None]], object]
    | None = None,
    thread_setter: Callable[[Callable[[threading.ExceptHookArgs], None]], object] | None = None,
    restore: Callable[[], None] | None = None,
) -> None:
    """Install idempotent total-guarded hooks for main and worker crashes."""
    try:
        if _STATE.installed:
            return
        stream = stderr if stderr is not None else sys.stderr
        restore_fn = restore if restore is not None else restore_terminal

        def _write_exception(
            exc_type: type[BaseException],
            exc_value: BaseException | None,
            exc_traceback: TracebackType | None,
        ) -> None:
            try:
                if exc_value is not None:
                    for line in traceback.format_exception(exc_type, exc_value, exc_traceback):
                        stream.write(strip_terminal_control(line))
                stream.flush()
            except Exception:
                pass
            with contextlib.suppress(Exception):
                restore_fn()

        def _sys_hook(
            exc_type: type[BaseException],
            exc_value: BaseException,
            exc_traceback: TracebackType | None,
        ) -> None:
            with contextlib.suppress(Exception):
                _write_exception(exc_type, exc_value, exc_traceback)

        def _thread_hook(args: threading.ExceptHookArgs) -> None:
            with contextlib.suppress(Exception):
                _write_exception(args.exc_type, args.exc_value, args.exc_traceback)

        (sys_setter if sys_setter is not None else _set_sys_hook)(_sys_hook)
        (thread_setter if thread_setter is not None else _set_thread_hook)(_thread_hook)
        _STATE.installed = True
    except Exception:
        pass


def _set_sys_hook(
    hook: Callable[[type[BaseException], BaseException, TracebackType | None], None],
) -> None:
    sys.excepthook = hook


def _set_thread_hook(hook: Callable[[threading.ExceptHookArgs], None]) -> None:
    threading.excepthook = hook
