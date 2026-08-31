"""Drive the conflict status bar from where the resolution has got to.

Thin adapters between the driver's loop counters and
:mod:`ralph.pipeline.conflict_resolution.status`, kept together because
they share the one awkward detail: the elapsed-time clock is read off
the display object itself and may not be there at all.

Split out of :mod:`ralph.pipeline.conflict_resolution.driver`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution.status import (
    clear_conflict_status_bar,
    push_conflict_status_bar,
    restore_status_bar,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop

__all__ = [
    "display_run_started_monotonic",
    "push_round_status",
    "restore_previous_status_bar",
]


def restore_previous_status_bar(
    display: ParallelDisplay | None, root: Path, previous_model: object | None
) -> None:
    """Put back whatever the status bar showed before the resolution began.

    ``None`` means there was nothing to put back, so the conflict label
    is cleared rather than restored -- leaving it up would report a
    resolution that is over as still running.
    """
    if previous_model is None:
        clear_conflict_status_bar(
            display,
            root,
            run_started_monotonic=display_run_started_monotonic(display),
        )
    else:
        restore_status_bar(display, previous_model)


def push_round_status(
    display: ParallelDisplay | None,
    root: Path,
    target: str,
    round_index: int,
    round_cap: int,
    stop: RebaseStop | None,
) -> None:
    """Show where the resolution is: which round, and which rebase stop.

    Every field a stop contributes is optional because the same driver
    runs for a standalone merge, which has no stop and therefore no
    replay position to report.
    """
    push_conflict_status_bar(
        display,
        root,
        target=target,
        round_index=round_index,
        round_cap=round_cap,
        stop_index=stop.stop_index if stop is not None else None,
        stop_cap=stop.stop_cap if stop is not None else None,
        replay_index=stop.replay_index if stop is not None else None,
        replay_total=stop.replay_total if stop is not None else None,
        run_started_monotonic=display_run_started_monotonic(display),
    )


def display_run_started_monotonic(display: ParallelDisplay | None) -> float | None:
    """The display's own run-start timestamp, or ``None`` if it has none.

    Read defensively: the attribute is absent on the fakes a test
    injects, and a missing elapsed clock must not stop the status bar
    from rendering.
    """
    if display is None:
        return None
    try:
        value: object = display.run_started_monotonic
    except AttributeError:
        return None
    return value if isinstance(value, float) else None
