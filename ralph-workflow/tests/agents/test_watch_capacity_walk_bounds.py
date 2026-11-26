from __future__ import annotations

import os
import sys
import threading
from typing import TYPE_CHECKING

import pytest

from ralph.agents import watch_capacity

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _tree(root: Path, count: int) -> None:
    for i in range(count):
        (root / f"d{i}").mkdir()


@pytest.mark.skipif(sys.platform != "linux", reason="inotify is Linux-only")
def test_the_prediction_is_reused_instead_of_rewalking_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tree(tmp_path, 5)
    walks: list[str] = []
    real_walk = os.walk

    def counting_walk(top: str) -> Iterator[tuple[str, list[str], list[str]]]:
        walks.append(str(top))
        return real_walk(top)

    monkeypatch.setattr(os, "walk", counting_walk)
    first = watch_capacity.watch_capacity_is_predicted(tmp_path, None, None, None)
    second = watch_capacity.watch_capacity_is_predicted(tmp_path, None, None, None)

    assert first == second
    assert len(walks) == 1


def test_a_walk_that_outlives_its_budget_stops_instead_of_finishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped = threading.Event()

    def endless_walk(top: str) -> Iterator[tuple[str, list[str], list[str]]]:
        try:
            while True:
                yield top, ["d"], []
        finally:
            stopped.set()

    monkeypatch.setattr(os, "walk", endless_walk)

    assert (
        watch_capacity.watch_capacity_is_predicted(tmp_path, 10**9, None, 0, budget_seconds=0.05)
        is True
    )
    assert stopped.wait(timeout=5.0)
