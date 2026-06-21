"""Cross-transport regression test for subagent visibility surface.

Every ``AgentTransport`` member must degrade gracefully to
``NullDiscoveryStrategy`` because no per-worker subagent log path is
currently documented. Independently, the ``IdleWatchdog`` public surface
that exposes real-time subagent progress must be present for every
transport.

The test uses direct API access (``_discovery_strategy_for_config`` and
``IdleWatchdog`` public methods); it does NOT call ``invoke_agent``,
so transports without a runtime resolver (``PI``) are included safely.

All tests use FakeClock; no real subprocess, no ``time.sleep``, no real
network.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
)
from ralph.agents.invoke._monitor_factory import _discovery_strategy_for_config
from ralph.agents.timeout_clock import FakeClock
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.process.monitor import NullDiscoveryStrategy


@dataclass
class _NoProcessMonitor:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


def _make_watchdog() -> IdleWatchdog:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor())


@pytest.mark.parametrize("transport", list(AgentTransport))
def test_discovery_strategy_is_null_for_transport(transport: AgentTransport) -> None:
    """``_discovery_strategy_for_config`` returns ``NullDiscoveryStrategy`` for
    every transport because no per-worker subagent log path is documented."""
    config = AgentConfig(cmd=transport.value, transport=transport)
    strategy = _discovery_strategy_for_config(config)
    assert isinstance(strategy, NullDiscoveryStrategy), (
        f"transport={transport!r}: expected NullDiscoveryStrategy;"
        f" got {type(strategy).__name__}"
    )


@pytest.mark.parametrize("transport", list(AgentTransport))
def test_idle_watchdog_subagent_surface_exists_for_transport(
    transport: AgentTransport,
) -> None:
    """The public subagent visibility surface is present for every transport.

    ``record_subagent_work`` stores progress, ``last_subagent_progress_description``
    exposes it, and ``register_default_subagent_activity_listener`` forwards
    it to a registered listener.
    """
    del transport  # included for documentation / future transport-specific hooks
    watchdog = _make_watchdog()
    captured: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured.append(event)

    watchdog.record_invocation_start()
    watchdog.register_default_subagent_activity_listener(_listener)

    assert watchdog.last_subagent_progress_description is None
    watchdog.record_subagent_work(description="reading source.py")
    assert watchdog.last_subagent_progress_description == "reading source.py"

    watchdog._emit(
        WaitingStatusKind.ENTERED,
        current_run_seconds=0.0,
        idle_elapsed=0.0,
        ceiling_seconds=60.0,
    )
    assert len(captured) == 1
    assert captured[0].subagent_activity == "reading source.py"
