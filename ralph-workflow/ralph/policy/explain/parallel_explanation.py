
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParallelExplanation:

    phase: str
    max_parallel_workers: int
    max_work_units: int
    require_allowed_directories: bool
    post_fanout_verification: bool = False
