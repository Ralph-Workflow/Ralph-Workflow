"""Pure helpers for failure-reason formatting in the pipeline slice."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.recovery.failure_category import FailureCategory

if TYPE_CHECKING:
    from ralph.pipeline.events import PhaseFailureEvent
    from ralph.pipeline.state import PipelineState


def failure_category_prefix(category: FailureCategory) -> str:
    prefix_map = {
        FailureCategory.ENVIRONMENTAL: "Environmental fault",
        FailureCategory.AGENT: "Agent fault",
        FailureCategory.USER_CONFIG: "Configuration fault",
        FailureCategory.ARTIFACT_VALIDATION: "Artifact validation fault",
        FailureCategory.AMBIGUOUS: "Ambiguous fault (flagged for review)",
    }
    return prefix_map.get(category, "Unknown fault")


def classified_failure_reason_for_event(event: PhaseFailureEvent) -> str:
    raw_message = event.reason or f"(no reason reported for phase={event.phase})"
    if event.failure_category is None:
        return raw_message
    return f"{failure_category_prefix(event.failure_category)}: {raw_message}"


def commit_failure_reason(state: PipelineState) -> str:
    return f"{state.phase}: Commit failed"
