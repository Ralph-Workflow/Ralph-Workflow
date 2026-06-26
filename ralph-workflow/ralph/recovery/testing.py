"""Test helpers for recovery package: fake monitors and fakes for black-box tests."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.recovery.connectivity import ConnectivityEvent, ConnectivityState

if TYPE_CHECKING:
    from collections.abc import Callable


class FakeConnectivityMonitor:
    """Deterministic connectivity monitor for tests.

    Allows injecting state transitions without real network probes.
    """

    def __init__(self, initial_state: ConnectivityState = ConnectivityState.ONLINE) -> None:
        self._state: ConnectivityState = initial_state
        self._listeners: list[  # bounded-accumulator-ok: drained
    Callable[[ConnectivityEvent], None]
] = []
        self._online_event: asyncio.Event = asyncio.Event()
        if initial_state == ConnectivityState.ONLINE:
            self._online_event.set()

    @property
    def current_state(self) -> ConnectivityState:
        return self._state

    def add_listener(self, cb: Callable[[ConnectivityEvent], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def _unsub() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(cb)

        return _unsub

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self._online_event.set()

    async def wait_online(self) -> None:
        await self._online_event.wait()

    def go_offline(self, reason: str = "test offline") -> None:
        """Simulate going offline."""
        if self._state != ConnectivityState.OFFLINE:
            self._state = ConnectivityState.OFFLINE
            self._online_event.clear()
            self._emit(ConnectivityState.OFFLINE, reason)

    def go_online(self, reason: str = "test online") -> None:
        """Simulate coming back online."""
        if self._state != ConnectivityState.ONLINE:
            self._state = ConnectivityState.ONLINE
            self._online_event.set()
            self._emit(ConnectivityState.ONLINE, reason)

    def _emit(self, state: ConnectivityState, reason: str) -> None:
        evt = ConnectivityEvent(state=state, since=datetime.now(UTC), reason=reason)
        for listener in list(self._listeners):
            with contextlib.suppress(Exception):
                listener(evt)
