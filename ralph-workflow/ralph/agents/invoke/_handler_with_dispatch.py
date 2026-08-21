"""_HandlerWithDispatch protocol for the per-monitor watchdog handler."""

from __future__ import annotations

from typing import Protocol


class _HandlerWithDispatch(Protocol):
    """Structural type of the per-monitor watchdog handler.

    The change tracker is a class with a public ``dispatch(event)``
    method; ``WorkspaceMonitor.dispatch_event`` routes test-supplied
    events through it. Declared as a Protocol so the ``cast`` at that
    call site needs no ``attr-defined`` suppression -- test files carry
    zero suppressions.
    """

    def dispatch(self, event: object) -> None: ...
