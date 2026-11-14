from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.rebase_state import RebaseState
    from tests._scenario_operator_line import OperatorLine


@dataclass(frozen=True)
class IntegrationRun:
    state: RebaseState | None
    error: str | None
    operator_lines: tuple[OperatorLine, ...] = ()
    resolver_launches: int = 0
    facts: dict[str, str] = field(default_factory=dict)

    @property
    def failed_with_exception(self) -> bool:
        return self.error is not None
