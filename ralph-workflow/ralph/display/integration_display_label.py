"""Canonical closed vocabulary for integration display labels.

Split from :mod:`ralph.display.auto_integrate_message` so neither
module carries two top-level classes (repo-structure policy).
"""

from __future__ import annotations

from enum import StrEnum


class IntegrationDisplayLabel(StrEnum):
    """The canonical closed vocabulary for the seven distinct integration display labels."""

    CONFLICT_RESOLUTION = "conflict resolution"
    EVIDENCE_DIAGNOSIS = "evidence diagnosis"
    INTEGRATION = "integration"
    LANDING = "landing"
    PUBLICATION = "publication"
    WAITING = "waiting"
    INTERVENTION = "intervention"


#: Producer-side action/state strings routed to a display label.
DISPLAY_LABEL_MAP: dict[str, str] = {
    "conflict_resolution": IntegrationDisplayLabel.CONFLICT_RESOLUTION.value,
    "conflict resolution": IntegrationDisplayLabel.CONFLICT_RESOLUTION.value,
    "conflict": IntegrationDisplayLabel.CONFLICT_RESOLUTION.value,
    "resolve_active_operation": IntegrationDisplayLabel.CONFLICT_RESOLUTION.value,
    "evidence_diagnosis": IntegrationDisplayLabel.EVIDENCE_DIAGNOSIS.value,
    "evidence diagnosis": IntegrationDisplayLabel.EVIDENCE_DIAGNOSIS.value,
    "diagnosing": IntegrationDisplayLabel.EVIDENCE_DIAGNOSIS.value,
    "diagnosis": IntegrationDisplayLabel.EVIDENCE_DIAGNOSIS.value,
    "integrating": IntegrationDisplayLabel.INTEGRATION.value,
    "integration": IntegrationDisplayLabel.INTEGRATION.value,
    "continue_git_operation": IntegrationDisplayLabel.INTEGRATION.value,
    "reintegrate_against_target": IntegrationDisplayLabel.INTEGRATION.value,
    "landing": IntegrationDisplayLabel.LANDING.value,
    "land_target": IntegrationDisplayLabel.LANDING.value,
    "publishing": IntegrationDisplayLabel.PUBLICATION.value,
    "publication": IntegrationDisplayLabel.PUBLICATION.value,
    "publish_after_landing": IntegrationDisplayLabel.PUBLICATION.value,
    "waiting": IntegrationDisplayLabel.WAITING.value,
    "wait_for_bounded_retry": IntegrationDisplayLabel.WAITING.value,
    "intervention_required": IntegrationDisplayLabel.INTERVENTION.value,
    "intervention": IntegrationDisplayLabel.INTERVENTION.value,
    "require_intervention": IntegrationDisplayLabel.INTERVENTION.value,
    "diagnostic_intervention_required": IntegrationDisplayLabel.INTERVENTION.value,
    "report_intervention_required": IntegrationDisplayLabel.INTERVENTION.value,
    "resolved": IntegrationDisplayLabel.LANDING.value,
    "recoverable": IntegrationDisplayLabel.WAITING.value,
    "exhausted": IntegrationDisplayLabel.INTERVENTION.value,
}

__all__ = ["DISPLAY_LABEL_MAP", "IntegrationDisplayLabel"]
