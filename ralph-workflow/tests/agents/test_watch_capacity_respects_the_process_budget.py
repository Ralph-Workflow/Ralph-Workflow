from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents import watch_capacity

if TYPE_CHECKING:
    from pathlib import Path

_HOST_LIMIT = 1_048_576


def _counter(directories: int):
    def count(_workspace: Path, *, cap: int) -> int | None:
        return None if directories >= cap else directories

    return count


def test_a_workspace_larger_than_the_process_watch_budget_is_never_watched(tmp_path: Path) -> None:
    too_many = watch_capacity.PROCESS_WATCH_BUDGET + 1

    assert watch_capacity.watch_capacity_is_predicted(tmp_path, _HOST_LIMIT, _counter(too_many), 0)


def test_a_workspace_within_the_process_budget_and_host_room_is_watched(tmp_path: Path) -> None:
    assert not watch_capacity.watch_capacity_is_predicted(tmp_path, _HOST_LIMIT, _counter(5_000), 0)


def test_the_directory_walk_stops_at_the_process_budget(tmp_path: Path) -> None:
    caps: list[int] = []

    def count(_workspace: Path, *, cap: int) -> int | None:
        caps.append(cap)
        return None

    watch_capacity.watch_capacity_is_predicted(tmp_path, _HOST_LIMIT, count, 0)

    assert caps == [watch_capacity.PROCESS_WATCH_BUDGET + 1]
