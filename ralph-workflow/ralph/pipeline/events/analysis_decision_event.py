from dataclasses import dataclass


@dataclass(frozen=True)
class AnalysisDecisionEvent:
    phase: str
    decision: str
