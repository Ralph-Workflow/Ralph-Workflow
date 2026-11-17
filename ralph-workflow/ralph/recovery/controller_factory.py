from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.recovery.controller import RecoveryController

if TYPE_CHECKING:
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.recovery.recovery_controller_options import RecoveryControllerOptions

__all__ = ("RecoveryController", "build_recovery_controller")


def build_recovery_controller(
    *,
    options: RecoveryControllerOptions | None = None,
    ledger: AgentFailureLedger | None = None,
) -> RecoveryController:
    return RecoveryController(options=options, ledger=ledger)
