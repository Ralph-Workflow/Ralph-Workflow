"""Structured failure events and event bus for recovery observability."""

from __future__ import annotations

import contextlib
from collections.abc import Callable

from loguru import logger

from ralph.recovery.failure_event import FailureEvent
from ralph.recovery.fallover_event import FalloverEvent

__all__ = ["FailureEvent", "FailureEventBus", "FalloverEvent", "UnsubscribeFn"]

UnsubscribeFn = Callable[[], None]

_AnyListener = Callable[[FailureEvent | FalloverEvent], None]


class FailureEventBus:
    """Simple publish/subscribe bus for failure and fallover events."""

    def __init__(self) -> None:
        self._listeners: list[_AnyListener] = []

    def publish(self, evt: FailureEvent | FalloverEvent) -> None:
        if isinstance(evt, FailureEvent):
            category: str = evt.category
            agent_field: str | None = evt.agent
            counted: bool | None = evt.counted_against_budget
        else:
            category = "fallover"
            agent_field = evt.from_agent
            counted = None
        logger.bind(recovery=True).debug(
            "Recovery event: category={} phase={} agent={} counted={}",
            category,
            evt.phase,
            agent_field,
            counted,
        )
        for listener in list(self._listeners):
            try:
                listener(evt)
            except Exception:
                logger.debug("FailureEventBus listener raised", exc_info=True)

    def subscribe(self, cb: _AnyListener) -> UnsubscribeFn:
        """Register a listener. Returns a callable that unsubscribes it."""
        self._listeners.append(cb)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(cb)

        return _unsubscribe
