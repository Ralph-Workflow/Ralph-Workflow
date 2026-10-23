from __future__ import annotations

from pathlib import Path

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    record_path,
)
from ralph.pipeline.operation_evidence import (
    InMemoryOperationEvidenceCollector,
    OperationFact,
)
from tests._resolver_controller import make_resolver_controller
from tests.test_auto_integrate_record_idempotent import _RecordBackend


def test_handoff_integration_diagnostic_repeated_missing_agents_persists_intervention_at_cap() -> (
    None
):
    backend = _RecordBackend()
    root = Path("/workspace")
    cap = diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
        merge_commit_repair_attempts=cap - 2,
    )
    invoked: list[str] = []

    def custom_invoke(agent: str, prompt: Path) -> bool:
        invoked.append(agent)
        return True

    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )

    # Attempt 1: bumps attempt count below cap
    assert not diagnostic.handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=root,
        record=record,
        failure="receipt unreadable 1",
        agents=(),
        invoke=custom_invoke,
        backend=backend,
        collector=collector,
    )
    saved1 = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert saved1.merge_commit_repair_attempts == cap - 1
    assert saved1.diagnostic_intervention_required is False
    assert saved1.repair_last_error == "no eligible diagnostic agent available"
    assert invoked == []

    # Attempt 2: reaches cap, raises intervention
    assert not diagnostic.handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=root,
        record=saved1,
        failure="receipt unreadable 2",
        agents=(),
        invoke=custom_invoke,
        backend=backend,
        collector=collector,
    )
    saved2 = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert saved2.merge_commit_repair_attempts == cap
    assert saved2.diagnostic_intervention_required is True
    assert "diagnostic repair exhausted" in (saved2.repair_cap_reason or "")
    assert invoked == []

    # Attempt 3: already at cap with intervention; zero later invocations occur
    assert not diagnostic.handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=root,
        record=saved2,
        failure="receipt unreadable 3",
        agents=(),
        invoke=custom_invoke,
        backend=backend,
        collector=collector,
    )
    saved3 = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert saved3.merge_commit_repair_attempts == cap
    assert saved3.diagnostic_intervention_required is True
    assert invoked == []
