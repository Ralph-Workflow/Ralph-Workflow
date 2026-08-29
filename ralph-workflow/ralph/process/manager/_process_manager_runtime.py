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
    return cast(
        "_PsutilModuleLike", _PsutilModuleAdapter(cast("_PsutilModuleLike", psutil_import))
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)


def redacted_command(command: Sequence[str]) -> str:
    """Render ``command`` for a log sink without echoing any argument value.

    Agent argv carries secrets in ordinary operation: the inline prompt body is
    appended as the trailing argument, and third-party credentials are embedded
    in the ``--mcp-config`` JSON. ``ralph.agents.invoke._commands._command_for_log``
    already strips the former before logging an invocation, so a process-lifecycle
    sink must not reintroduce the leak. The executable names the failure; the
    arguments only ever add exposure, so they are counted rather than shown.
    """
    if not command:
        return "<empty>"
    remaining = len(command) - 1
    if remaining == 0:
        return f"{command[0]!r}"
    return f"{command[0]!r} (+{remaining} argument{'s' if remaining != 1 else ''} withheld)"


def loguru_event_listener(event: ProcessEvent) -> None:
    record = event.record
    new_status = event.new_status
    bound = logger.bind(component="process", pid=record.pid, label=record.label)
    if new_status in (ProcessStatus.SPAWNED, ProcessStatus.RUNNING):
        bound.debug("process {} {} rc={}", record.pid, new_status.name, record.returncode)
    elif new_status == ProcessStatus.EXITED:
        bound.info("process {} {} rc={}", record.pid, new_status.name, record.returncode)
    elif new_status == ProcessStatus.KILLED:
        if record.cause == "zombie_reconciled" and record.returncode == 0:
            bound.debug("process {} {} rc={}", record.pid, new_status.name, record.returncode)
        else:
            bound.warning("process {} {} rc={}", record.pid, new_status.name, record.returncode)
    elif new_status == ProcessStatus.FAILED:
        # A spawn that never produced a child has pid -1 and rc None, so those
        # two fields alone say nothing an operator can act on. The executable and
        # the failure message are the whole diagnosis.
        bound.error(
            "process {} {} rc={} command={} failure={}",
            record.pid,
            new_status.name,
            record.returncode,
            redacted_command(record.command),
            record.failure_message,
        )


__all__ = ["load_psutil_module", "loguru_event_listener"]
