from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests._scenario_operator_line import OperatorLine


@dataclass
class ScenarioResult:
    resolver_launches: int
    operator_lines: tuple[OperatorLine, ...]
    terminal_state: str
    facts: dict[str, str] = field(default_factory=dict)
