from __future__ import annotations

from enum import StrEnum


class IntegrationDisplayLabel(StrEnum):
    CONFLICT_RESOLUTION = "conflict resolution"
    EVIDENCE_DIAGNOSIS = "evidence diagnosis"
    INTEGRATION = "integration"
    LANDING = "landing"
    PUBLICATION = "publication"
    WAITING = "waiting"
    INTERVENTION = "intervention"


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
    "ladder_escalation": IntegrationDisplayLabel.EVIDENCE_DIAGNOSIS.value,
    "resolved": IntegrationDisplayLabel.LANDING.value,
    "recoverable": IntegrationDisplayLabel.WAITING.value,
    "exhausted": IntegrationDisplayLabel.INTERVENTION.value,
}

__all__ = ["DISPLAY_LABEL_MAP", "IntegrationDisplayLabel"]
