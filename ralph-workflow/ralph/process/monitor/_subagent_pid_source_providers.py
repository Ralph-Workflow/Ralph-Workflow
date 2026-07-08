"""Per-transport ``SubagentPidSource`` providers backed by ``SubagentPidRegistry``.

The canonical ``SubagentPidSource`` Protocol lives in
``ralph/process/monitor/_subagent_pid_source.py`` and is the integration
seam the watchdog consumes. This module provides per-transport factory
helpers that wrap the shared :class:`SubagentPidRegistry` (defined in
``ralph/agents/idle_watchdog/_subagent_identity.py``) so any transport
that extracts a subagent PID from its structured output events can
register the PID into the same canonical registry the watchdog defers
on.

The factory helpers return objects that satisfy the
``SubagentPidSource`` Protocol (``known_subagent_pids() -> set[int]``)
and the helpers themselves are the single place where new transport
adapters should plug in. New transports add a new factory helper here;
existing transports keep using their existing PID source (OpenCode
uses ``ChildLivenessSubagentPidSource`` directly because its structured
child events carry the PID natively -- there is no need to re-register
into ``SubagentPidRegistry``).

Lifecycle:

    - A single :class:`SubagentPidRegistry` is constructed per
      invocation (typically by the ``AgentRegistry.from_config`` site or
      the process monitor factory).
    - Per-transport factories wrap that registry to expose
      ``known_subagent_pids()`` to the ``ProcessMonitor``.
    - The registry is FIFO-bounded at ``_MAX_REGISTRY_ENTRIES = 1024``
      so a long-lived invocation cannot leak heavyweight identity
      records across runs.

The factory helpers below do NOT mutate global state; they are pure
constructors that bind the registry reference and return a Protocol-
conforming adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog import SubagentPidRegistry
    from ralph.process.monitor import SubagentPidSource


class _RegistryBackedSubagentPidSource:
    """Adapter that exposes a ``SubagentPidRegistry`` as a ``SubagentPidSource``.

    Returned by every ``make_*_subagent_pid_source`` factory in this
    module. Satisfies the ``SubagentPidSource`` Protocol by delegating
    ``known_subagent_pids()`` to the underlying registry.

    The adapter is a thin proxy -- it does NOT own the registry, does
    NOT mutate it, and does NOT add eviction or filtering logic on top
    of the registry's contract. Callers register PIDs via the
    registry's own ``register`` method; reads happen through the
    adapter's ``known_subagent_pids()``.
    """

    __slots__ = ("_registry", "_source_label")

    def __init__(self, registry: SubagentPidRegistry, source_label: str) -> None:
        self._registry = registry
        self._source_label = source_label

    def known_subagent_pids(self) -> set[int]:
        """Return the PIDs registered for ``self._source_label``.

        Filters the registry's snapshot to entries whose
        ``SubagentIdentity.source`` matches this adapter's
        ``_source_label`` so a registry shared across multiple
        transports never leaks a Claude-registered PID into an
        OpenCode monitor (and vice versa).
        """
        return {
            identity.pid
            for identity in self._registry.snapshot()
            if identity.source == self._source_label
        }

    def __repr__(self) -> str:
        return f"_RegistryBackedSubagentPidSource(source={self._source_label!r})"


def make_opencode_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    """Build a registry-backed ``SubagentPidSource`` for the OpenCode transport.

    OpenCode typically uses ``ChildLivenessSubagentPidSource`` directly
    because its structured child events carry the PID natively. This
    factory is provided so a registry-aware OpenCode pipeline (one that
    already populates a shared ``SubagentPidRegistry``) can also feed
    the monitor via the registry seam.
    """
    return _RegistryBackedSubagentPidSource(registry, "opencode")


def make_claude_subagent_pid_source(
    registry: SubagentPidRegistry,
) -> SubagentPidSource:
    """Build a registry-backed ``SubagentPidSource`` for the Claude transport."""
    return _RegistryBackedSubagentPidSource(registry, "claude")


def make_pi_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    """Build a registry-backed ``SubagentPidSource`` for the pi transport."""
    return _RegistryBackedSubagentPidSource(registry, "pi")


def make_agy_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    """Build a registry-backed ``SubagentPidSource`` for the AGY transport."""
    return _RegistryBackedSubagentPidSource(registry, "agy")


def make_claude_interactive_subagent_pid_source(
    registry: SubagentPidRegistry,
) -> SubagentPidSource:
    """Build a registry-backed ``SubagentPidSource`` for the Claude-interactive transport."""
    return _RegistryBackedSubagentPidSource(registry, "claude_interactive")


def make_codex_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    """Build a registry-backed ``SubagentPidSource`` for the Codex transport."""
    return _RegistryBackedSubagentPidSource(registry, "codex")


def make_generic_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    """Build a registry-backed ``SubagentPidSource`` for the generic transport."""
    return _RegistryBackedSubagentPidSource(registry, "generic")


def make_nanocoder_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    """Build a registry-backed ``SubagentPidSource`` for the Nanocoder transport.

    Nanocoder shares the generic wire format (it has no per-transport
    structured child events), but the watchdog's per-transport
    ``SubagentPidSource`` filter (R1) is keyed on the ``AgentTransport``
    enum, not on the parser. The factory binds the canonical
    ``"nanocoder"`` source label so a Nanocoder-registered PID is
    isolated from Generic-registered PIDs in the shared
    ``SubagentPidRegistry`` (the R1 isolation invariant).
    """
    return _RegistryBackedSubagentPidSource(registry, "nanocoder")


def make_cursor_subagent_pid_source(registry: SubagentPidRegistry) -> SubagentPidSource:
    """Build a registry-backed ``SubagentPidSource`` for the Cursor transport.

    Binds the canonical ``"cursor"`` source label so any PID registered
    by :class:`CursorParser` is isolated from PIDs registered by other
    transports' parsers in the shared :class:`SubagentPidRegistry`
    (the R1 isolation invariant).  Cursor's documented ``stream-json``
    envelope does not currently include a ``pid`` field; the
    registry-backed source is forward-compat for events that carry
    one.
    """
    return _RegistryBackedSubagentPidSource(registry, "cursor")


__all__ = [
    "make_agy_subagent_pid_source",
    "make_claude_interactive_subagent_pid_source",
    "make_claude_subagent_pid_source",
    "make_codex_subagent_pid_source",
    "make_cursor_subagent_pid_source",
    "make_generic_subagent_pid_source",
    "make_nanocoder_subagent_pid_source",
    "make_opencode_subagent_pid_source",
    "make_pi_subagent_pid_source",
]
