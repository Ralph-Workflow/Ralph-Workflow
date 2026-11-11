from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions


def make_resolver_controller(clock: FakeClock | None = None) -> RecoveryController:
    return RecoveryController(options=RecoveryControllerOptions(clock=clock or FakeClock()))
