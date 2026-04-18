"""Base types for agent output parsing.

This module defines the protocol that all parser modules must implement.
`AgentOutputLine` remains available for backward compatibility as legacy
parser output while the typed activity model is introduced separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True, slots=True)
class AgentOutputLine:
    """Legacy normalised line extracted from agent NDJSON stream.

    This type is preserved for backward compatibility while newer cross-layer
    visibility work adopts the typed activity model.

    Attributes:
        type: Type of the output line (text, tool_use, tool_result, error, etc.).
        content: Text content of the line.
        raw: Raw JSON string from the agent.
        metadata: Additional metadata extracted from the line.
    """

    type: str
    content: str = ""
    raw: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class AgentParser(Protocol):
    """Protocol all parser modules must implement.

    A parser takes raw lines from an agent's stdout and yields
    normalized AgentOutputLine instances.
    """

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse agent output lines.

        Args:
            lines: Iterator of raw lines from agent stdout.

        Yields:
            Normalized AgentOutputLine instances.
        """
        ...
