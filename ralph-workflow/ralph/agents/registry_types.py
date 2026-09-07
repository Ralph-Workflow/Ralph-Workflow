"""Typed registry entry shared by agent catalog and execution dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents._contracts import StrategyFactory
    from ralph.agents.parsers.base import AgentParser
    from ralph.config.enums import AgentTransport


@dataclass
class ParserRegistryEntry:
    """Pair a parser factory with its transport-specific strategy factory."""

    parser_factory: Callable[[], AgentParser]
    strategy_factory: StrategyFactory
    transport: AgentTransport

    def __call__(self, **_kwargs: object) -> AgentParser:
        return self.parser_factory()


__all__ = ["ParserRegistryEntry"]
