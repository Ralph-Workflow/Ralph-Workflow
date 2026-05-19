"""Fan-out pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.work_units import WorkUnit
else:
    WorkUnit = import_module("ralph.pipeline.work_units").WorkUnit


@dataclass(frozen=True)
class FanOutEffect:
    """Effect to fan out parallel work for any phase whose [parallelization] policy is declared.

    Workers run in the shared checkout. Each worker is restricted to its declared
    ``allowed_directories`` and writes its outputs to a per-worker namespace under
    ``.agent/workers/<unit_id>/``.

    Attributes:
        work_units: Work units to execute in parallel.
        max_workers: Maximum number of concurrent workers.
        run_post_fanout_verification: When True, the runner will execute a serialized
            workspace-wide verification step after all workers finish. Defaults to False
            so unit tests do not invoke ``make verify``.
        phase: The pipeline phase for which fan-out is occurring. Defaults to empty
            string for backward compatibility; the runner always populates this.
    """

    work_units: tuple[WorkUnit, ...]
    max_workers: int
    run_post_fanout_verification: bool = False
    phase: str = ""
