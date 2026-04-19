"""Parallel display skeleton with TTY/CI mode detection.

Provides ParallelDisplay, a context manager that routes output to either a
live dashboard (rich, TTY) or plain line-by-line output (CI, dumb terminals).
Rendering logic is filled in by subsequent tasks (T24 for emit, T25 for
set_status).
"""

from __future__ import annotations

import os
import queue
import signal
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol, cast

from loguru import logger
from rich.console import Console
from rich.live import Live

from ralph.display.render_thread import RenderThread, UpdateEvent
from ralph.display.renderers.dashboard import DashboardState, render_dashboard
from ralph.pipeline.worker_state import WorkerStatus

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from rich.console import RenderableType

from ralph.display.mode import NARROW_THRESHOLD, detect_mode

type SignalHandler = Callable[[int, object], None] | int | None


def _noop_sigwinch(signum: int, frame: object) -> None:
    logger.debug("SIGWINCH received, letting rich handle resize on next refresh")


_STATUS_SUFFIX = "__status__"
_UNATTRIBUTED_UNIT_ID = "activity"


class _ConsolePrint(Protocol):
    def __call__(self, console: Console, *args: object, **kwargs: object) -> None: ...


_ORIGINAL_CONSOLE_PRINT = cast("_ConsolePrint", Console.print)


class _DashboardConsole(Console):
    """Isolated console for live dashboard control writes."""

    def print(self, *args: object, **kwargs: object) -> None:
        _ORIGINAL_CONSOLE_PRINT(self, *args, **kwargs)


def _coerce_worker_status(raw_status: object) -> WorkerStatus:
    if isinstance(raw_status, WorkerStatus):
        return raw_status
    if isinstance(raw_status, str):
        try:
            return WorkerStatus(raw_status)
        except ValueError:
            return WorkerStatus.RUNNING
    return WorkerStatus.RUNNING


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _elapsed_seconds(runtime: Mapping[str, object] | None) -> float:
    if runtime is None:
        return 0.0

    started_at = runtime.get("started_at")
    if not isinstance(started_at, datetime):
        return 0.0

    finished_at = runtime.get("finished_at")
    if isinstance(finished_at, datetime):
        return (finished_at - started_at).total_seconds()

    return (_now_utc() - started_at).total_seconds()


def _dashboard_renderable(
    state: dict[str, list[str] | str],
    worker_status: Mapping[str, Mapping[str, object]] | None = None,
) -> RenderableType:
    dashboard_state: dict[str, DashboardState] = {}
    worker_status = {} if worker_status is None else worker_status
    unit_ids = {
        key.removesuffix(_STATUS_SUFFIX) if key.endswith(_STATUS_SUFFIX) else key
        for key in state
    }

    for unit_id in sorted(unit_ids):
        lines_obj = state.get(unit_id)
        lines = lines_obj if isinstance(lines_obj, list) else []
        status = _coerce_worker_status(
            state.get(f"{unit_id}{_STATUS_SUFFIX}", WorkerStatus.RUNNING)
        )
        runtime = worker_status.get(unit_id)
        dashboard_state[unit_id] = {
            "unit_id": (
                _UNATTRIBUTED_UNIT_ID if unit_id == "__unattributed__" else unit_id
            ),
            "status": status,
            "elapsed_s": _elapsed_seconds(runtime),
            "last_output": lines[-1] if lines else "",
            "dropped": 0,
            # TODO(T15): wire RingBuffer.dropped_count once log_tail panel is the
            # source of truth
        }

    return render_dashboard(dashboard_state)


class ParallelDisplay:
    """Display manager for parallel pipeline workers.

    Detects at construction time whether to run in "dashboard" mode (rich live
    rendering, TTY) or "lines" mode (plain line-by-line output, CI/dumb).
    The mode is frozen after __init__ and cannot be changed.

    Args:
        console: Rich Console instance used for rendering and TTY detection.
        env: Environment mapping used for mode detection.  Defaults to
            os.environ when None.
    """

    __slots__ = (
        "_console",
        "_mode",
        "_prev_sigwinch",
        "_queue",
        "_render_thread",
        "_worker_status",
    )

    def __init__(
        self,
        console: Console,
        env: Mapping[str, str] | None = None,
    ) -> None:
        resolved_env = dict(os.environ if env is None else env)
        self._console = console
        self._mode: Literal["dashboard", "lines"] = detect_mode(console, resolved_env)
        self._queue: queue.Queue[UpdateEvent] = queue.Queue()
        self._render_thread: RenderThread | None = None
        self._prev_sigwinch: SignalHandler = None
        self._worker_status: dict[str, dict[str, object]] = {}

    @property
    def mode(self) -> Literal["dashboard", "lines"]:
        """Display mode, frozen after construction."""
        return self._mode

    def start(self) -> None:
        if self._mode == "dashboard":
            live_console = _DashboardConsole(
                file=self._console.file,
                force_terminal=self._console.is_terminal,
                width=self._console.width,
            )
            live = Live(console=live_console, auto_refresh=False)
            self._render_thread = RenderThread(
                q=self._queue,
                renderable_fn=lambda state: _dashboard_renderable(state, self._worker_status),
                live=live,
            )
            self._render_thread.start()
            if hasattr(signal, "SIGWINCH"):
                self._prev_sigwinch = cast(
                    "SignalHandler", signal.signal(signal.SIGWINCH, _noop_sigwinch)
                )

    def stop(self) -> None:
        if self._render_thread is not None:
            self._render_thread.stop()
            self._render_thread = None
        if hasattr(signal, "SIGWINCH") and self._prev_sigwinch is not None:
            signal.signal(signal.SIGWINCH, cast("SignalHandler", self._prev_sigwinch))

    def emit(self, unit_id: str | None, line: str) -> None:
        if self._mode == "dashboard":
            self._queue.put(UpdateEvent(unit_id=unit_id, kind="output", payload=line))
        else:
            prefix = f"[{unit_id}] " if unit_id else ""
            self._console.out(f"{prefix}{line}")

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        if self._mode == "dashboard":
            runtime = self._worker_status.setdefault(
                unit_id,
                {"status": status, "started_at": None, "finished_at": None},
            )
            now = _now_utc()
            runtime["status"] = status
            if status == WorkerStatus.RUNNING and runtime.get("started_at") is None:
                runtime["started_at"] = now
            elif status in {
                WorkerStatus.SUCCEEDED,
                WorkerStatus.FAILED,
                WorkerStatus.CANCELLED,
            }:
                if runtime.get("started_at") is None:
                    runtime["started_at"] = now
                runtime["finished_at"] = now
            self._queue.put(UpdateEvent(unit_id=unit_id, kind="status", payload=str(status)))
        else:
            self._console.out(f"[{unit_id}] status={status}")

    def __enter__(self) -> ParallelDisplay:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.stop()


__all__ = ["NARROW_THRESHOLD", "ParallelDisplay", "detect_mode"]
