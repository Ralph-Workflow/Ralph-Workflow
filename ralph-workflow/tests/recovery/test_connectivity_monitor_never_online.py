"""The real connectivity monitor only reports OFFLINE after it has seen the network.

A host whose egress blocks the probe targets (corporate proxy-only network,
offline local-model use) never reaches them at all. That is "probe targets
unreachable", not "connectivity lost": reporting OFFLINE would pause the run
before every iteration forever. The monitor therefore stays UNKNOWN until a
probe first succeeds, and only a failure AFTER that is a real OFFLINE.
"""

from __future__ import annotations

import asyncio

import pytest

from ralph.recovery.connectivity import ConnectivityEvent, ConnectivityMonitor, ConnectivityState

pytestmark = pytest.mark.timeout_seconds(5)

_PROBE_ROUNDS = 5


class _ScriptedProbe:
    """Probe whose reachability answers follow a script, then repeat the last one."""

    def __init__(self, answers: list[bool]) -> None:
        self._answers = answers
        self.calls = 0

    async def __call__(self, host: str, port: int, timeout_s: float) -> bool:
        del host, port, timeout_s
        index = min(self.calls, len(self._answers) - 1)
        self.calls += 1
        return self._answers[index]


async def _run_rounds(monitor: ConnectivityMonitor, probe: _ScriptedProbe, rounds: int) -> None:
    await monitor.start()
    while probe.calls < rounds:
        await asyncio.sleep(0)
    await monitor.stop()


def _monitor(probe: _ScriptedProbe) -> ConnectivityMonitor:
    return ConnectivityMonitor(
        probe_targets=[("probe.invalid", 53)], probe_interval_s=0.0, probe=probe
    )


def test_targets_unreachable_since_start_is_unknown_not_offline() -> None:
    probe = _ScriptedProbe([False])
    monitor = _monitor(probe)
    events: list[ConnectivityEvent] = []
    monitor.add_listener(events.append)

    asyncio.run(_run_rounds(monitor, probe, _PROBE_ROUNDS))

    # The run loop pauses only on OFFLINE, so UNKNOWN lets the run proceed.
    assert monitor.current_state == ConnectivityState.UNKNOWN
    assert all(evt.state != ConnectivityState.OFFLINE for evt in events)


def test_loss_after_being_online_is_still_offline() -> None:
    probe = _ScriptedProbe([True, False])
    monitor = _monitor(probe)
    states: list[ConnectivityState] = []
    monitor.add_listener(lambda evt: states.append(evt.state))

    asyncio.run(_run_rounds(monitor, probe, _PROBE_ROUNDS))

    assert monitor.current_state == ConnectivityState.OFFLINE
    assert states == [ConnectivityState.ONLINE, ConnectivityState.OFFLINE]


def test_first_success_after_unreachable_start_reports_online() -> None:
    probe = _ScriptedProbe([False, False, True])
    monitor = _monitor(probe)

    asyncio.run(_run_rounds(monitor, probe, _PROBE_ROUNDS))

    assert monitor.current_state == ConnectivityState.ONLINE
