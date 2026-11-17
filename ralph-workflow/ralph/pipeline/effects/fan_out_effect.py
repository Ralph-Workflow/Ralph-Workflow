from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.work_units import WorkUnit


@dataclass(frozen=True)
class FanOutEffect:
    work_units: tuple[WorkUnit, ...]
    max_workers: int
    run_post_fanout_verification: bool = False
    phase: str = ""
