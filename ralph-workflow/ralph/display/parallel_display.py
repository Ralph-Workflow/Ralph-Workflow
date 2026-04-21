"""Parallel display adapter: always emit log-first, copy-paste-safe transcript lines."""

from __future__ import annotations

import os
import queue
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ralph.display.mode import NARROW_THRESHOLD, detect_mode
from ralph.display.phase_banner import show_phase_transition
from ralph.display.plain_renderer import PlainLogRenderer
from ralph.display.subscriber import PipelineSubscriber
from ralph.display.theme import make_console as _make_console

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from rich.console import Console

    from ralph.display.snapshot import PipelineSnapshot
    from ralph.pipeline.worker_state import WorkerStatus

_DEFAULT_SNAPSHOT_QUEUE_MAXSIZE: int = 64


def _strip_markup(line: str) -> str:
    return PlainLogRenderer.strip_markup(line)


class ParallelDisplay:
    __slots__ = (
        "_console",
        "_mode",
        "_plain_renderer",
        "_subscriber",
    )

    def __init__(  # noqa: PLR0913
        self,
        console: Console | None = None,
        env: Mapping[str, str] | None = None,
        *,
        mode: Literal["lines"] | None = None,
        subscriber: PipelineSubscriber | None = None,
        workspace_root: Path | None = None,
        run_id: str | None = None,
    ) -> None:
        resolved_env = dict(os.environ if env is None else env)
        if console is None:
            console = _make_console()
        self._console = console
        self._mode: Literal["lines"] = "lines"
        if mode is None:
            self._mode = detect_mode(console, resolved_env)

        self._plain_renderer = PlainLogRenderer(console)

        if subscriber is not None:
            self._subscriber = subscriber
        else:
            snapshot_q: queue.Queue[PipelineSnapshot] = queue.Queue(
                maxsize=_DEFAULT_SNAPSHOT_QUEUE_MAXSIZE
            )
            effective_root = workspace_root if workspace_root is not None else Path.cwd()
            effective_run_id = run_id if run_id is not None else str(uuid.uuid4())
            self._subscriber = PipelineSubscriber(
                queue=snapshot_q,
                workspace_root=effective_root,
                run_id=effective_run_id,
                on_snapshot=self._plain_renderer.emit_snapshot,
            )

    @property
    def mode(self) -> Literal["lines"]:
        return self._mode

    @property
    def subscriber(self) -> PipelineSubscriber:
        return self._subscriber

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def emit(self, unit_id: str | None, line: str) -> None:
        self._plain_renderer.emit_log_line(unit_id or "activity", line)

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        self._plain_renderer.emit_status_line(unit_id, str(status))

    def emit_analysis_result(
        self,
        phase: str,
        decision: str,
        reason: str | None = None,
    ) -> None:
        # Only record to decision_log via subscriber; the titled block is rendered
        # by render_analysis_decision in the phase handler (development.py/review.py).
        # This avoids double-rendering both a plain [analysis] line and a titled block.
        try:
            self._subscriber.record_analysis(phase, decision, reason)
        except Exception:
            return None

    def emit_phase_transition(self, from_phase: str, to_phase: str) -> None:
        show_phase_transition(from_phase, to_phase, console=self._console)
        try:
            self._subscriber.record_phase_transition(from_phase, to_phase)
        except Exception:
            return None

    @property
    def console(self) -> Console:
        """Expose console for external renderers."""
        return self._console

    def __enter__(self) -> ParallelDisplay:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        self.stop()


__all__ = ["NARROW_THRESHOLD", "ParallelDisplay", "_strip_markup", "detect_mode"]
