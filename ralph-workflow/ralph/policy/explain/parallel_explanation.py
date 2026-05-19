"""Explanation of the parallel execution policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParallelExplanation:
    """Explanation of the parallel execution policy."""

    phase: str
    max_parallel_workers: int
    max_work_units: int
    require_allowed_directories: bool
    post_fanout_verification: bool = False
