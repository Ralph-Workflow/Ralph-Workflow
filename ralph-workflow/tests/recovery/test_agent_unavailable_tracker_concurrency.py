"""The shared cooldown ledger is read and written from several threads at once."""

from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_unavailability_tracker import AgentUnavailabilityTracker
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from collections.abc import Iterator

_WRITERS = 4
_ROUNDS = 2000


@pytest.fixture
def aggressive_thread_switching() -> Iterator[None]:
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


@pytest.mark.usefixtures("aggressive_thread_switching")
def test_concurrent_marking_and_snapshotting_never_corrupts_the_ledger() -> None:
    clock = FakeClock()
    tracker = AgentUnavailabilityTracker(clock=clock)
    errors: list[BaseException] = []

    def writer(index: int) -> None:
        try:
            for round_index in range(_ROUNDS):
                agent = f"agent-{index}-{round_index % 25}"
                tracker.mark_unavailable("p", agent, UnavailabilityReason.PROCESS_EXITED)
                clock.advance(10_000.0)
                tracker.reset_backoff("p", agent)
        except BaseException as exc:
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(_ROUNDS):
                tracker.snapshot()
                tracker.is_available("p", "agent-0-0")
                tracker.earliest_unavailable_wait_ms("p", ["agent-0-0", "agent-1-1"])
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(_WRITERS)]
    threads.append(threading.Thread(target=reader))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
