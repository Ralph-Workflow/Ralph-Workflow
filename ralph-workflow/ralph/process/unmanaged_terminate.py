from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.process.killable_process_like import KillableProcessLike

if TYPE_CHECKING:
    from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer

__all__ = [
    "KillableProcessLike",
    "TerminableProcessLike",
    "kill_unmanaged",
    "terminate_unmanaged",
]


@runtime_checkable
class TerminableProcessLike(Protocol):
    def terminate(
        self,
        grace_period_s: float | None = None,
        *,
        issuer: ProcessTerminationIssuer,
    ) -> None: ...


def terminate_unmanaged(
    proc: TerminableProcessLike,
    *,
    issuer: ProcessTerminationIssuer,
    grace_period_s: float | None = None,
) -> None:
    proc.terminate(grace_period_s=grace_period_s, issuer=issuer)


def kill_unmanaged(
    proc: KillableProcessLike,
    *,
    issuer: ProcessTerminationIssuer,
) -> None:
    proc.kill(issuer=issuer)
