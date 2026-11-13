from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer


@runtime_checkable
class KillableProcessLike(Protocol):
    def kill(self, *, issuer: ProcessTerminationIssuer) -> None: ...
