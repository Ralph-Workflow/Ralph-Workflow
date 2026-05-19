"""Analysis decision event for the pipeline."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AnalysisDecisionEvent:
    """Event emitted when an analysis phase resolves a decision from the agent artifact.

    The reducer routes via ``policy.phases[phase].decisions[decision].target`` directly,
    making this a first-class routing input rather than a collapsed signal.

    Attributes:
        phase: Name of the phase that emitted this decision.
        decision: Raw decision string from the agent artifact (validated against
            the phase's decision_vocabulary in the artifacts policy).
    """

    phase: str
    decision: str
