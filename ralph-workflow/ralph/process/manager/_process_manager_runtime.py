from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.process.manager._process_status import ProcessStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.process.manager._process_event import ProcessEvent
    from ralph.process.manager._process_manager_types import _PsutilModuleLike, _PsutilProcessLike


class _PsutilModuleAdapter:
    def __init__(self, module: _PsutilModuleLike) -> None:
        self._module = module

    def process_from_pid(self, pid: int) -> _PsutilProcessLike:
        return self._module.Process(pid)

    def __getattr__(self, name: str) -> object:
        return cast("object", getattr(self._module, name))


def load_psutil_module() -> _PsutilModuleLike | None:
    try:
        psutil_import = importlib.import_module("psutil")
    except ModuleNotFoundError:
        return None
    return cast("_PsutilModuleLike", _PsutilModuleAdapter(cast("_PsutilModuleLike", psutil_import)))


def redacted_command(command: Sequence[str]) -> str:
    if not command:
        return "<empty>"
    remaining = len(command) - 1
    if remaining == 0:
        return f"{command[0]!r}"
    return f"{command[0]!r} (+{remaining} argument{'s' if remaining != 1 else ''} withheld)"


def loguru_event_listener(event: ProcessEvent) -> None:
    record = event.record
    new_status = event.new_status
    if new_status in (ProcessStatus.SPAWNED, ProcessStatus.RUNNING) or (
        new_status in (ProcessStatus.EXITED, ProcessStatus.KILLED) and record.returncode == 0
    ):
        return

    bound = logger.bind(component="process", pid=record.pid, label=record.label)
    if new_status == ProcessStatus.KILLED or (
        new_status == ProcessStatus.EXITED and record.returncode != 0
    ):
        bound.warning("process {} {} rc={}", record.pid, new_status.name, record.returncode)
    elif new_status == ProcessStatus.FAILED:
        bound.error(
            "process {} {} rc={} command={} failure={}",
            record.pid,
            new_status.name,
            record.returncode,
            redacted_command(record.command),
            record.failure_message,
        )


__all__ = ["load_psutil_module", "loguru_event_listener"]
