"""Shared fixture for tests that drive the resolver handoffs directly."""

from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions


def make_resolver_controller(clock: FakeClock | None = None) -> RecoveryController:
    """Return a controller whose unavailability ledger runs on a fake clock."""
    return RecoveryController(options=RecoveryControllerOptions(clock=clock or FakeClock()))
