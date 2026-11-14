from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from tests._scenario_result import ScenarioResult

__all__ = ["ScenarioRunner"]


class ScenarioRunner(Protocol):
    def __call__(self, root: Path) -> ScenarioResult: ...
