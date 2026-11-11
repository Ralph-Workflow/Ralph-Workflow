
from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ._subagent_output_capture import SubagentOutputCapture


class ProcessRole(StrEnum):

    HOST = "host"
    SPAWNED_SUBAGENT = "spawned_subagent"
    INCIDENTAL_HELPER = "incidental_helper"
    UNKNOWN = "unknown"


@runtime_checkable
class ClassifiedProcess(Protocol):

    @property
    def pid(self) -> int: ...

    @property
    def role(self) -> ProcessRole: ...

    @property
    def cmdline(self) -> list[str] | None: ...


@runtime_checkable
class ProcessMonitor(Protocol):

    def live_subagent_count(self) -> int:
        ...

    def spawned_subagent_count(self) -> int:
        ...

    def classified_processes(self) -> tuple[ClassifiedProcess, ...]:
        ...

    def refresh(self) -> None:
        ...

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        ...
