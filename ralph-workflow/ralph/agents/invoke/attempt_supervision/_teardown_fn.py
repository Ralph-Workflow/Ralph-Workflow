from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer


class _TeardownFn(Protocol):
    def __call__(self, host_pid: int, /, *, issuer: ProcessTerminationIssuer) -> None: ...
