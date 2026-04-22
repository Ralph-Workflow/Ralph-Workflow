"""Proactive connectivity detection with auto-resume."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class ConnectivityState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass
class ConnectivityEvent:
    state: ConnectivityState
    since: datetime
    reason: str


# Default probe targets: DNS resolvers over TCP
_DEFAULT_PROBE_TARGETS: list[tuple[str, int]] = [
    ("1.1.1.1", 53),
    ("8.8.8.8", 53),
]
_DEFAULT_PROBE_INTERVAL_S: float = 10.0
_DEFAULT_PROBE_TIMEOUT_S: float = 2.0


async def _default_probe(host: str, port: int, timeout_s: float) -> bool:
    """Attempt TCP connection; return True if reachable."""
    try:
        pair: tuple[asyncio.StreamReader, asyncio.StreamWriter] = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout_s,
        )
        writer = pair[1]
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True
    except Exception:
        return False


ProbeCallable = Callable[[str, int, float], Awaitable[bool]]
ListenerCallable = Callable[[ConnectivityEvent], None]


class ConnectivityMonitor:
    """Proactively detect connectivity loss and surface state transitions.

    All timing and network I/O is injectable so tests run deterministically
    without real sockets.
    """

    def __init__(
        self,
        *,
        probe_targets: list[tuple[str, int]] = _DEFAULT_PROBE_TARGETS,
        probe_interval_s: float = _DEFAULT_PROBE_INTERVAL_S,
        probe_timeout_s: float = _DEFAULT_PROBE_TIMEOUT_S,
        probe: ProbeCallable | None = None,
    ) -> None:
        self._targets = probe_targets
        self._interval = probe_interval_s
        self._timeout = probe_timeout_s
        self._probe = probe or _default_probe
        self._current_state: ConnectivityState = ConnectivityState.UNKNOWN
        self._listeners: list[ListenerCallable] = []
        self._task: asyncio.Task[None] | None = None
        self._online_event: asyncio.Event = asyncio.Event()
        self._stopped: bool = False

    @property
    def current_state(self) -> ConnectivityState:
        return self._current_state

    def add_listener(self, cb: ListenerCallable) -> Callable[[], None]:
        """Register a listener for connectivity events. Returns an unsubscribe callable."""
        self._listeners.append(cb)

        def _unsubscribe() -> None:
            self._listeners.remove(cb)

        return _unsubscribe

    async def start(self) -> None:
        """Start the background connectivity probe loop."""
        self._stopped = False
        self._task = asyncio.create_task(self._probe_loop())

    async def stop(self) -> None:
        """Stop the background probe loop and unblock any waiters."""
        self._stopped = True
        self._online_event.set()  # unblock any wait_online() callers
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def wait_online(self) -> None:
        """Suspend until connectivity is restored (or monitor is stopped)."""
        await self._online_event.wait()

    async def _probe_once(self) -> bool:
        """Probe all targets; return True if any are reachable."""
        results = await asyncio.gather(
            *[self._probe(host, port, self._timeout) for host, port in self._targets],
            return_exceptions=True,
        )
        return any(r is True for r in results)

    async def _probe_loop(self) -> None:
        while not self._stopped:
            reachable = await self._probe_once()
            new_state = ConnectivityState.ONLINE if reachable else ConnectivityState.OFFLINE
            if new_state != self._current_state:
                self._current_state = new_state
                if new_state == ConnectivityState.ONLINE:
                    self._online_event.set()
                else:
                    self._online_event.clear()
                evt = ConnectivityEvent(
                    state=new_state,
                    since=datetime.now(UTC),
                    reason="probe" if reachable else "all probes failed",
                )
                for listener in list(self._listeners):
                    with contextlib.suppress(Exception):
                        listener(evt)
            await asyncio.sleep(self._interval)
