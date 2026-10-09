# Deprecated: Ralph-orchestrated workers are removed from the execution model; parallelism is owned by the developer agent's own sub-agents. See docs/sphinx/concepts.md §"Deprecated: Ralph-orchestrated workers".
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
