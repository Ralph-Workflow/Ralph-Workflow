from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, TypeVar, cast, overload

from ralph.agents._contracts import StrategyFactory
from ralph.agents.execution_state._strategy_builders import (
    _make_agy_strategy,
    _make_cursor_strategy,
    _make_kimi_strategy,
    _make_pi_strategy,
)
from ralph.agents.execution_state.claude_execution_strategy import ClaudeExecutionStrategy
from ralph.agents.execution_state.claude_interactive_execution_strategy import (
    ClaudeInteractiveExecutionStrategy,
)
from ralph.agents.execution_state.generic_execution_strategy import GenericExecutionStrategy
from ralph.agents.execution_state.opencode_execution_strategy import OpenCodeExecutionStrategy
from ralph.agents.parsers.agy import AgyParser
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.parsers.codex import CodexParser
from ralph.agents.parsers.cursor import CursorParser
from ralph.agents.parsers.gemini import GeminiParser
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.parsers.kimi import KimiParser
from ralph.agents.parsers.nanocoder import NanocoderParser
from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.agents.parsers.pi import PiParser
from ralph.agents.registry_types import ParserRegistryEntry
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.ccs_config import CcsConfig
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from ralph.agents.execution_state._base import BaseExecutionStrategy
    from ralph.agents.parsers.base import AgentParser

__all__ = [
    "AgentCatalog",
    "_CatalogBackedMapping",
    "_ParserRegistryEntry",
    "_reset_default_catalog",
    "default_catalog",
]


K = TypeVar("K")
V = TypeVar("V")
_T = TypeVar("_T")


_ParserRegistryEntry = ParserRegistryEntry


class _BuiltinModule(Protocol):
    def builtin_supports(self) -> tuple[AgentSupport, ...]: ...


class _RegistryModule(Protocol):
    def _resolve_dynamic_agent(
        self,
        name: str,
        ccs_defaults: CcsConfig,
        *,
        base_lookup: Callable[[str], AgentConfig | None] | None = None,
    ) -> AgentConfig | None: ...


def _builtin_supports() -> tuple[AgentSupport, ...]:
    module = cast("_BuiltinModule", import_module("ralph.agents.builtin"))
    return module.builtin_supports()


class _FlexibleFactory:
    __slots__ = ("_factory",)

    def __init__(self, factory: Callable[[], "AgentParser"]) -> None:
        self._factory = factory

    def __call__(self, **kwargs: object) -> "AgentParser":
        try:
            return self._factory(**kwargs)
        except TypeError:
            return self._factory()


class _CatalogBackedMapping(Mapping[K, V]):
    __slots__ = ("_getter",)

    def __init__(self, getter: Callable[[], Mapping[K, V]]) -> None:
        self._getter = getter

    def __getitem__(self, key: K) -> V:
        return self._getter()[key]

    def __len__(self) -> int:
        return len(self._getter())

    def __iter__(self) -> Iterator[K]:
        return iter(self._getter())

    def __contains__(self, key: object) -> bool:
        return key in self._getter()

    def __repr__(self) -> str:
        return f"_CatalogBackedMapping({self._getter()!r})"

    @overload
    def get(self, key: K, /) -> V | None: ...

    @overload
    def get(self, key: K, default: V, /) -> V: ...

    @overload
    def get(self, key: K, default: _T, /) -> V | _T: ...

    def get(self, key: K, default: _T | None = None) -> V | _T | None:
        if default is None:
            without_default: V | None = self._getter().get(key)
            return without_default
        with_default: V | _T = self._getter().get(key, default)
        return with_default

    def __setitem__(self, key: K, value: V) -> None:
        msg = f"catalog-backed mapping does not support item assignment ({key!r})"
        raise TypeError(msg)

    def __delitem__(self, key: K) -> None:
        msg = f"catalog-backed mapping does not support item deletion ({key!r})"
        raise TypeError(msg)


_DEFAULT_BUILTIN_PARSER_TYPES: dict[
    str, tuple[type["AgentParser"], "AgentTransport", "StrategyFactory"]
] = {
    "agy": (AgyParser, AgentTransport.AGY, _make_agy_strategy),
    "claude": (ClaudeParser, AgentTransport.CLAUDE, ClaudeExecutionStrategy),
    "claude_interactive": (
        ClaudeInteractiveParser,
        AgentTransport.CLAUDE_INTERACTIVE,
        ClaudeInteractiveExecutionStrategy,
    ),
    "codex": (CodexParser, AgentTransport.CODEX, GenericExecutionStrategy),
    "cursor": (CursorParser, AgentTransport.CURSOR, GenericExecutionStrategy),
    "gemini": (GeminiParser, AgentTransport.GENERIC, GenericExecutionStrategy),
    "kimi": (KimiParser, AgentTransport.KIMI, _make_kimi_strategy),
    "nanocoder": (NanocoderParser, AgentTransport.NANOCODER, GenericExecutionStrategy),
    "opencode": (OpenCodeParser, AgentTransport.OPENCODE, GenericExecutionStrategy),
    "pi": (PiParser, AgentTransport.PI, _make_pi_strategy),
    "generic": (GenericParser, AgentTransport.GENERIC, GenericExecutionStrategy),
}


@dataclass
class _AgentCatalogState:
    parsers: dict[str, "type[AgentParser] | _ParserRegistryEntry"] = field(default_factory=dict)
    commands: dict[str, "Callable[[], AgentParser] | _ParserRegistryEntry"] = field(
        default_factory=dict
    )
    strategies: dict[AgentTransport, StrategyFactory] = field(default_factory=dict)


def _default_strategy_factories() -> dict[AgentTransport, StrategyFactory]:
    return {
        AgentTransport.OPENCODE: OpenCodeExecutionStrategy,
        AgentTransport.CLAUDE: ClaudeExecutionStrategy,
        AgentTransport.CLAUDE_INTERACTIVE: ClaudeInteractiveExecutionStrategy,
        AgentTransport.AGY: _make_agy_strategy,
        AgentTransport.CODEX: GenericExecutionStrategy,
        AgentTransport.NANOCODER: GenericExecutionStrategy,
        AgentTransport.PI: _make_pi_strategy,
        AgentTransport.CURSOR: _make_cursor_strategy,
        AgentTransport.KIMI: _make_kimi_strategy,
        AgentTransport.GENERIC: GenericExecutionStrategy,
    }


@dataclass
class AgentCatalog:
    _entries: dict[str, "AgentSupport"] = field(default_factory=dict)
    _by_command: dict[str, "AgentSupport"] = field(default_factory=dict)
    _state: _AgentCatalogState = field(default_factory=_AgentCatalogState)

    def _default_strategy_for_transport(self, transport: AgentTransport) -> "StrategyFactory":
        factory = self._default_strategies.get(transport)
        if factory is None:
            msg = f"No default strategy registered for transport {transport!r}"
            raise ValueError(msg)
        return factory

    @property
    def _default_strategies(self) -> dict[AgentTransport, "StrategyFactory"]:
        return _default_strategy_factories()

    _DEFAULT_STRATEGIES = _default_strategies

    def add(self, support: "AgentSupport") -> None:
        name_lower = support._name_lower
        if name_lower in self._entries:
            msg = f"Agent {support.name!r} is already registered"
            raise ValueError(msg)

        if self._is_built_in_parser_key(name_lower) and not support.is_builtin:
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
        builtin_names = {s.name.lower() for s in _builtin_supports()}
        if key in builtin_names:
            return True

        existing = default_catalog()._state.parsers.get(key)
        return existing is not None and not isinstance(existing, _ParserRegistryEntry)

    def _write_through(self, support: "AgentSupport", name_lower: str, cmd_lower: str) -> None:
        entry = _ParserRegistryEntry(
            support.parser_factory,
            support.strategy_factory,
            support.spec.transport,
        )
        existing_parser = self._state.parsers.get(name_lower)
        if existing_parser is None or isinstance(existing_parser, _ParserRegistryEntry):
            self._state.parsers[name_lower] = entry
        existing_custom = self._state.commands.get(cmd_lower)
        if existing_custom is None or isinstance(existing_custom, _ParserRegistryEntry):
            self._state.commands[cmd_lower] = entry
        if support.spec.transport not in self._state.strategies:
            self._state.strategies[support.spec.transport] = support.strategy_factory

    def remove(self, name: str) -> None:
        name_lower = name.lower()
        support = self._entries.pop(name_lower, None)
        if support is not None:
            cmd_lower = support.cmd.lower()
            self._by_command.pop(cmd_lower, None)
            self._state.parsers.pop(name_lower, None)
            self._state.commands.pop(cmd_lower, None)
            if self._state.strategies.get(support.spec.transport) is support.strategy_factory:
                self._state.strategies.pop(support.spec.transport, None)

    def replace_builtin(self, name: str, support: AgentSupport) -> None:
        if not support.is_builtin:
            msg = f"Replacement support must have is_builtin=True: {name!r}"
            raise ValueError(msg)

        name_lower = name.lower()
        existing = self._entries.get(name_lower)
        if existing is None:
            msg = f"Cannot replace non-existent catalog entry: {name!r}"
            raise ValueError(msg)
        if not existing.is_builtin:
            msg = f"Cannot replace non-built-in catalog entry through replace_builtin: {name!r}"
            raise ValueError(msg)

        old_cmd_lower = existing.cmd.lower()
        new_cmd_lower = support.cmd.lower()

        self._entries.pop(name_lower, None)
        if old_cmd_lower != new_cmd_lower:
            self._by_command.pop(old_cmd_lower, None)
        self._state.commands.pop(old_cmd_lower, None)

        self._entries[name_lower] = support
        self._by_command[new_cmd_lower] = support
        self._write_through(support, name_lower, new_cmd_lower)

    def get(self, name_or_command: str) -> "AgentSupport | None":
        key = name_or_command.lower()
        support = self._entries.get(key)
        if support is not None:
            return support
        support = self._by_command.get(key)
        if support is not None:
            return support
        return _resolve_dynamic_support(self, name_or_command)

    def get_parser(self, name_or_command: str) -> "AgentParser":
        support = self.get(name_or_command)
        if support is None:
            msg = f"Unknown agent: {name_or_command}"
            raise ValueError(msg)
        return support.parser_factory()

    def get_strategy(
        self,
        transport: AgentTransport,
        command: str | None = None,
    ) -> "BaseExecutionStrategy":
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
        return tuple(sorted(self._entries.keys()))

    def by_transport(self, transport: AgentTransport) -> tuple["AgentSupport", ...]:
        return tuple(s for s in self._entries.values() if s.spec.transport == transport)


def _seed_default_catalog(catalog: AgentCatalog) -> None:
    if catalog._state.parsers:
        return

    for name, (factory, _transport, _strategy) in _DEFAULT_BUILTIN_PARSER_TYPES.items():
        catalog._state.parsers[name] = factory
    for support in _builtin_supports():
        name_lower = support.name.lower()
        cmd_lower = support.cmd.lower()
        catalog._entries[name_lower] = support
        catalog._by_command[cmd_lower] = support
        catalog._state.commands[cmd_lower] = support.parser_factory
    catalog._state.strategies.update(catalog._default_strategies)


_catalog_holder: list[AgentCatalog] = []


def default_catalog() -> AgentCatalog:
    if not _catalog_holder:
        catalog = AgentCatalog()
        _seed_default_catalog(catalog)
        _catalog_holder.append(catalog)
    return _catalog_holder[0]


def _reset_default_catalog(catalog: AgentCatalog) -> None:
    _catalog_holder.clear()
    _catalog_holder.append(catalog)


def _resolve_dynamic_support(catalog: AgentCatalog, name_or_command: str) -> "AgentSupport | None":
    def _catalog_base_lookup(agent_name: str) -> AgentConfig | None:
        entry = catalog._entries.get(agent_name.lower())
        if entry is not None:
            return entry.config
        return None

    registry = cast("_RegistryModule", import_module("ralph.agents.registry"))
    config = registry._resolve_dynamic_agent(
        name_or_command,
        CcsConfig(),
        base_lookup=_catalog_base_lookup,
    )
    if config is None:
        return None

    base: AgentSupport | None = None
    for entry in catalog._entries.values():
        if entry.spec.transport == config.transport:
            base = entry
            break
    if base is None:
        base = catalog._by_command.get(config.cmd.lower())
    if base is None:
        return None

    session_identifier_observable = base.session_identifier_observable
    if name_or_command.lower().startswith("ccs/"):
        session_identifier_observable = False

    return AgentSupport(
        name=name_or_command.lower(),
        spec=base.spec,
        parser_factory=base.parser_factory,
        strategy_factory=base.strategy_factory,
        config=config,
        is_builtin=base.is_builtin,
        no_default_session_flag=base.no_default_session_flag,
        display_capabilities=base.display_capabilities,
        session_identifier_observable=session_identifier_observable,
    )
