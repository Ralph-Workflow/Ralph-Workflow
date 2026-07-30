"""The decision the policy-remediation analysis agent returns.

Carries both the routing decision and the concrete feedback that becomes the
NEXT remediation prompt's input, which is what closes the loop: analysis does not
merely say "try again", it says exactly what came up short and how to fix it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.project_policy.pipeline_graph import DECISION_COMPLETED


@dataclass(frozen=True, slots=True)
class AnalysisDecision:
    """One ``policy_remediation_analysis_decision`` artifact, parsed.

    Attributes:
        status: One of the decision vocabulary (``completed``,
            ``request_changes``, ``failed``). A missing, corrupt, or
            out-of-vocabulary artifact is normalized to ``failed`` by
            :func:`ralph.project_policy.analysis.read_analysis_decision` -- never
            to ``completed``.
        summary: The agent's one-line account of what the review found.
        what_came_up_short: What is wrong with the policy the remediation agent
            wrote. Empty for a ``completed`` decision.
        how_to_fix: Concrete steps to resolve each item. Empty for a
            ``completed`` decision.
    """

    status: str
    summary: str = ""
    what_came_up_short: list[str] = field(default_factory=list[str])
    how_to_fix: list[str] = field(default_factory=list[str])

    def is_completed(self) -> bool:
        """True when the analysis agent accepted the policy as it stands."""
        return self.status == DECISION_COMPLETED

    def feedback_lines(self) -> list[str]:
        """Render the feedback as prompt-ready lines for the next remediation.

        Empty when the agent raised nothing, so the remediation prompt can omit
        the analysis-feedback section entirely rather than render an empty one.
        """
        return [
            *(f"- came up short: {item}" for item in self.what_came_up_short),
            *(f"- how to fix: {item}" for item in self.how_to_fix),
        ]


__all__ = ["AnalysisDecision"]
