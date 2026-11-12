from __future__ import annotations

from dataclasses import dataclass, field

from ralph.project_policy.pipeline_graph import DECISION_COMPLETED


@dataclass(frozen=True, slots=True)
class AnalysisDecision:
    status: str
    summary: str = ""
    what_came_up_short: list[str] = field(default_factory=list[str])
    how_to_fix: list[str] = field(default_factory=list[str])

    def is_completed(self) -> bool:
        return self.status == DECISION_COMPLETED

    def feedback_lines(self) -> list[str]:
        return [f"- came up short: {item}" for item in self.what_came_up_short]


__all__ = ["AnalysisDecision"]
