"""Per-worker log sink helpers for Ralph Workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


@dataclass(frozen=True)
class WorkerSinkHandle:
    """Handle returned by ``bind_worker_sink`` to identify a per-worker loguru sink."""

    sink_id: int
    log_path: Path


def bind_worker_sink(
    unit_id: str,
    log_dir: Path,
    run_id: str = "default",
) -> WorkerSinkHandle:
    """Add a per-worker loguru sink that filters to ``unit_id`` and returns its handle."""
    worker_log_dir = log_dir / run_id / "workers"
    worker_log_dir.mkdir(parents=True, exist_ok=True)
    log_path = worker_log_dir / f"unit-{unit_id}.log"

    def worker_filter(record: object) -> bool:
        record_mapping = cast("Mapping[str, object]", record)
        extra = cast("Mapping[str, object]", record_mapping["extra"])
        unit_id_value = extra.get("unit_id")
        return isinstance(unit_id_value, str) and unit_id_value == unit_id

    sink_id = logger.add(
        log_path,
        filter=worker_filter,
        format="{time} {level} {message}",
        buffering=8192,
    )
    return WorkerSinkHandle(sink_id=sink_id, log_path=log_path)


def remove_worker_sink(handle: WorkerSinkHandle) -> None:
    """Remove the per-worker loguru sink identified by ``handle``."""
    logger.remove(handle.sink_id)


__all__ = ["WorkerSinkHandle", "bind_worker_sink", "remove_worker_sink"]
