from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown.specs._plan_step_target import StepTarget


class PlanStep(TypedDict, total=False):
    id: str
    number: int
    depends_on: list[int]
    targets: list[StepTarget]


__all__: list[str] = []
