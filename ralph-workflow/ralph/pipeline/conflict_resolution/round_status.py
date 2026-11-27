from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution.status import push_conflict_status_bar

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop

__all__ = [
    "close_status_bar_scope",
    "display_run_started_monotonic",
    "push_round_status",
]


def close_status_bar_scope(scope: AbstractContextManager[None]) -> None:
    scope.__exit__(None, None, None)


def push_round_status(
    display: ParallelDisplay | None,
    root: Path,
    target: str,
    round_index: int,
    round_cap: int,
    stop: RebaseStop | None,
) -> None:
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
    if display is None:
        return None
    try:
        value: object = display.run_started_monotonic
    except AttributeError:
        return None
    return value if isinstance(value, float) else None
