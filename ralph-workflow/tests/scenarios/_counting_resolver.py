from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop, RebaseStopResolver


class CountingResolver:
    def __init__(self, inner: RebaseStopResolver) -> None:
        self.inner = inner
        self.launches = 0
        self.stops: list[RebaseStop] = []

    def __call__(self, root: Path, target: str, stop: RebaseStop) -> bool:
        self.launches += 1
        self.stops.append(stop)
        return bool(self.inner(root, target, stop))
