from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.process.manager._process_status import ProcessStatus

if TYPE_CHECKING:
    from ralph.process.manager._process_event import ProcessEvent
    from ralph.process.manager._process_manager_types import _PsutilModuleLike


def load_psutil_module() -> _PsutilModuleLike | None:
    try:
        psutil_import = importlib.import_module("psutil")
    except ModuleNotFoundError:
        return None
    return cast("_PsutilModuleLike", psutil_import)


def loguru_event_listener(event: ProcessEvent) -> None:
    record = event.record
    new_status = event.new_status
    bound = logger.bind(component="process", pid=record.pid, label=record.label)
    if new_status in (ProcessStatus.SPAWNED, ProcessStatus.RUNNING):
        bound.debug("process {} {} rc={}", record.pid, new_status.name, record.returncode)
    elif new_status == ProcessStatus.EXITED:
        bound.info("process {} {} rc={}", record.pid, new_status.name, record.returncode)
    elif new_status == ProcessStatus.KILLED:
        bound.warning("process {} {} rc={}", record.pid, new_status.name, record.returncode)
    elif new_status == ProcessStatus.FAILED:
        bound.error("process {} {} rc={}", record.pid, new_status.name, record.returncode)


__all__ = ["load_psutil_module", "loguru_event_listener"]
