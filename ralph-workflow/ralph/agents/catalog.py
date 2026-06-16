"""Instance-owned injectable registry for agent support registrations.

The AgentCatalog is instance-owned. Tests should construct a fresh AgentCatalog
per test rather than mutating module-level state.
The legacy 3 module-level dicts (_PARSER_REGISTRY, _CUSTOM_COMMAND_REGISTRY,
_STRATEGY_DISPATCH) are write-through state populated atomically by
AgentCatalog.add() and are deprecated; they will be removed in a future release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.agents.execution_state._factory import (
    _STRATEGY_DISPATCH,
    _STRATEGY_DISPATCH_DATA,
)
from ralph.agents.parsers import (
    _CUSTOM_COMMAND_REGISTRY_DATA,
    _PARSER_REGISTRY,
    _PARSER_REGISTRY_DATA,
    _ParserRegistryEntry,
)

if TYPE_CHECKING:
    from ralph.agents.execution_state._base import BaseExecutionStrategy
    from ralph.agents.parsers.base import AgentParser
    from ralph.agents.support import AgentSupport
    from ralph.config.enums import AgentTransport

__all__ = ["AgentCatalog", "_reset_default_catalog", "default_catalog"]


@dataclass
class AgentCatalog:
    """Single injectable agent registry.

    Manages registrations through AgentSupport objects. The legacy module-level
    dicts are write-through state populated atomically by ``add()`` for backward
    compatibility.
    """

    _entries: dict[str, AgentSupport] = field(default_factory=dict)
    _by_command: dict[str, AgentSupport] = field(default_factory=dict)

    def add(self, support: AgentSupport) -> None:
        """Register an agent support entry.

        Raises ValueError if the name or command is already registered,
        or if the name matches a reserved built-in parser key.
        """
        name_lower = support._name_lower
        if name_lower in self._entries:
            msg = f"Agent {support.name!r} is already registered"
            raise ValueError(msg)

        if self._is_built_in_parser_key(name_lower):
            msg = f"Cannot register agent with reserved built-in parser name: {support.name}"
            raise ValueError(msg)

        cmd_lower = support.cmd.lower()
        if cmd_lower in self._by_command:
            existing = self._by_command[cmd_lower]
            msg = f"Command {support.cmd!r} is already registered for agent {existing.name!r}"
            raise ValueError(msg)

        self._entries[name_lower] = support
        self._by_command[cmd_lower] = support
        self._write_through(support, name_lower, cmd_lower)

    @staticmethod
    def _is_built_in_parser_key(key: str) -> bool:
        """Return True when ``key`` maps to a built-in parser class.

        Custom registrations store a :class:`_ParserRegistryEntry`, so any existing
        class value is a built-in that must not be overwritten.
        """
        existing = _PARSER_REGISTRY.get(key)
        return existing is not None and not isinstance(existing, _ParserRegistryEntry)

    def _write_through(self, support: AgentSupport, name_lower: str, cmd_lower: str) -> None:
        """Populate the 3 legacy module-level dicts for backward compat.

        Only overwrites ``_STRATEGY_DISPATCH`` when the transport does not yet
        have an entry, preserving built-in dispatch entries so custom-agent
        registrations do not corrupt global transport-level strategy lookups.
        """
        entry = _ParserRegistryEntry(
            support.parser_factory,
            support.strategy_factory,
            support.spec.transport,
        )
        _PARSER_REGISTRY_DATA[name_lower] = entry
        if support.spec.transport not in _STRATEGY_DISPATCH:
            _STRATEGY_DISPATCH_DATA[support.spec.transport] = support.strategy_factory
        _CUSTOM_COMMAND_REGISTRY_DATA[cmd_lower] = entry

    def remove(self, name: str) -> None:
        """Remove an agent registration by name.

        Reverses the compatibility writes made by ``add()``: removes the parser
        entry from ``_PARSER_REGISTRY``, removes the command entry from
        ``_CUSTOM_COMMAND_REGISTRY``, and removes the transport strategy entry
        from ``_STRATEGY_DISPATCH`` only when the stored factory matches this
        registration's strategy factory (preserving built-in transport fallbacks
        for entries that existed before the removed registration).
        """
        name_lower = name.lower()
        support = self._entries.pop(name_lower, None)
        if support is not None:
            cmd_lower = support.cmd.lower()
            self._by_command.pop(cmd_lower, None)
            _PARSER_REGISTRY_DATA.pop(name_lower, None)
            _CUSTOM_COMMAND_REGISTRY_DATA.pop(cmd_lower, None)
            if _STRATEGY_DISPATCH.get(support.spec.transport) is support.strategy_factory:
                del _STRATEGY_DISPATCH_DATA[support.spec.transport]

    def get(self, name_or_command: str) -> AgentSupport | None:
        """Look up by agent name first, then by command."""
        key = name_or_command.lower()
        support = self._entries.get(key)
        if support is not None:
            return support
        return self._by_command.get(key)

    def get_parser(self, name_or_command: str) -> AgentParser:
        """Return a fresh parser instance for the given name or command."""
        support = self.get(name_or_command)
        if support is None:
            msg = f"Unknown agent: {name_or_command}"
            raise ValueError(msg)
        return support.parser_factory()

    def get_strategy(
        self,
        transport: AgentTransport,
        command: str | None = None,
    ) -> BaseExecutionStrategy:
        """Return a strategy instance by transport, optionally preferring a command match.

        Args:
            transport: The transport to look up.
            command: Optional command string. When provided and a matching
                registration exists, that registration's strategy factory is used.
                Otherwise falls back to the first entry with matching transport.

        Returns:
            A BaseExecutionStrategy instance.

        Raises:
            ValueError: If no matching strategy is found.
        """
        if command is not None:
            cmd_lower = command.lower()
            match = self._by_command.get(cmd_lower)
            if match is not None:
                return match.strategy_factory(label_scope=None, registry=None)

        for support in self._entries.values():
            if support.spec.transport == transport:
                return support.strategy_factory(label_scope=None, registry=None)

        msg = f"No strategy found for transport {transport!r}"
        raise ValueError(msg)

    def list_agents(self) -> tuple[str, ...]:
        """Return sorted tuple of registered agent names."""
        return tuple(sorted(self._entries.keys()))

    def by_transport(self, transport: AgentTransport) -> tuple[AgentSupport, ...]:
        """Return all supports matching the given transport."""
        return tuple(s for s in self._entries.values() if s.spec.transport == transport)


_catalog_holder: list[AgentCatalog] = []


def default_catalog() -> AgentCatalog:
    if not _catalog_holder:
        _catalog_holder.append(AgentCatalog())
    return _catalog_holder[0]


def _reset_default_catalog(catalog: AgentCatalog) -> None:
    _catalog_holder.clear()
    _catalog_holder.append(catalog)
