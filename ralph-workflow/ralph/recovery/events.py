"""Structured failure events and event bus for recovery observability."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger

UnsubscribeFn = Callable[[], None]


class FailureEventBus:
    """Simple publish/subscribe bus for failure and fallover events."""

    @dataclass(frozen=True)
    class FailureEvent:
        """Structured failure event emitted for every classified failure."""

        timestamp: datetime
        phase: str
        agent: str | None
        category: str
        reason: str
        counted_against_budget: bool
        chain_capacity_remaining: int
        recovery_cycle: int
        retry_delay_ms: int = 0

    @dataclass(frozen=True)
    class FalloverEvent:
        """Emitted when an agent is exhausted and the chain falls over to the next."""

        timestamp: datetime
        phase: str
        from_agent: str
        to_agent: str
        reason: str

        @classmethod
        def now(cls, *, phase: str, from_agent: str, to_agent: str, reason: str) -> FalloverEvent:
            return cls(
                timestamp=datetime.now(UTC),
                phase=phase,
                from_agent=from_agent,
                to_agent=to_agent,
                reason=reason,
            )


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


FailureEvent = FailureEventBus.FailureEvent
FalloverEvent = FailureEventBus.FalloverEvent

_AnyListener = Callable[[FailureEvent | FalloverEvent], None]
