"""Kimi ``SubagentPidSource`` factory contract (R1 isolation invariant).

The kimi transport binds the canonical ``"kimi"`` source label through
:func:`ralph.process.monitor.make_kimi_subagent_pid_source`, mirroring the
cursor factory.  The tests pin the R1 isolation invariant: a PID registered
under ``"kimi"`` is visible to the kimi-filtered source, while a PID
registered under any other transport's source label is invisible to it.
"""

from __future__ import annotations

from ralph.agents.idle_watchdog import SubagentPidRegistry
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.process.monitor import SubagentPidSource, make_kimi_subagent_pid_source


def test_make_kimi_subagent_pid_source_filters_by_kimi_label() -> None:
    """The factory binds the ``"kimi"`` source label on a shared registry."""
    registry = SubagentPidRegistry()
    source = make_kimi_subagent_pid_source(registry)

    assert isinstance(source, SubagentPidSource)

    registry.register(12345, source="kimi", now=0.0)
    assert 12345 in source.known_subagent_pids()

    # A PID registered under a DIFFERENT transport label is invisible (R1).
    registry.register(67890, source="cursor", now=0.0)
    assert 67890 not in source.known_subagent_pids()


def test_make_kimi_subagent_pid_source_isolates_against_pi_label() -> None:
    """Cross-transport isolation also holds for the pi label (enum neighbours)."""
    registry = SubagentPidRegistry()
    source = make_kimi_subagent_pid_source(registry)

    registry.register(4242, source="pi", now=0.0)
    assert 4242 not in source.known_subagent_pids()


def test_registry_factory_binds_kimi_transport() -> None:
    """``AgentRegistry.build_subagent_pid_registry`` dispatches KIMI to the factory."""
    agent_registry = AgentRegistry()
    registry, source = agent_registry.build_subagent_pid_registry(AgentTransport.KIMI)

    assert isinstance(registry, SubagentPidRegistry)
    assert isinstance(source, SubagentPidSource)

    registry.register(111, source="kimi", now=0.0)
    assert 111 in source.known_subagent_pids()

    registry.register(222, source="nanocoder", now=0.0)
    assert 222 not in source.known_subagent_pids()


def test_registry_factory_accepts_kimi_by_string_value() -> None:
    """The transport may also be addressed by its raw enum value string."""
    agent_registry = AgentRegistry()
    registry, source = agent_registry.build_subagent_pid_registry("kimi")

    registry.register(333, source="kimi", now=0.0)
    assert 333 in source.known_subagent_pids()
