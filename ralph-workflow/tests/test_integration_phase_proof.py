"""Phase-proof arms for the atomic integration transition check."""

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.pipeline.auto_integrate_record import IntegrationRecord


def test_has_phase_proof_integrated_phase_without_sha() -> None:
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
    )
    assert diagnostic._has_phase_proof(record) is True


def test_has_phase_proof_integrating_with_sha() -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        integrated_feature_sha="abc123",
    )
    assert diagnostic._has_phase_proof(record) is True


def test_has_phase_proof_integrating_without_sha() -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
    )
    assert diagnostic._has_phase_proof(record) is False
