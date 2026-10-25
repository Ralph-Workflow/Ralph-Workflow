"""Exhaustion tests for the integration diagnostic.

The repair-attempt cap is the bound that terminates the diagnostic
rotation: once it is reached the durable record carries the typed
"diagnostic budget spent" outcome and the state escalates into the
autonomous fallback ladder (durable-ledger rungs) -- never a human
hand-off.
"""

from __future__ import annotations

from pathlib import Path

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    record_path,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.operation_evidence import (
    InMemoryOperationEvidenceCollector,
    OperationFact,
)
from tests._integration_ladder_fakes import _FakePlumbing
from tests._resolver_controller import make_resolver_controller
from tests.test_auto_integrate_record_idempotent import _RecordBackend


def test_handoff_integration_diagnostic_repeated_missing_agents_persists_intervention_at_cap(
    tmp_path: Path,
) -> None:
    backend = _RecordBackend()
    root = Path("/workspace")
    ledger_path = tmp_path / "attempt-ledger.json"
    ledger = AttemptLedger(root, ledger_path=ledger_path)
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
    # The ladder dispatch after the cap uses the injected plumbing fake:
    # no real Git, no subprocess in this unit test.
    plumbing = _FakePlumbing(changed=())

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
        ledger=ledger,
    )
    saved1 = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert saved1.merge_commit_repair_attempts == cap - 1
    assert saved1.diagnostic_intervention_required is False
    assert saved1.repair_last_error == "no eligible diagnostic agent available"
    assert invoked == []

    # Attempt 2: reaches cap, stamps the typed budget-spent outcome and
    # escalates into the autonomous fallback ladder (recorded in the ledger).
    assert not diagnostic.handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=root,
        record=saved1,
        failure="receipt unreadable 2",
        agents=(),
        invoke=custom_invoke,
        backend=backend,
        collector=collector,
        ledger=ledger,
        git=plumbing,
    )
    saved2 = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert saved2.merge_commit_repair_attempts == cap
    assert saved2.diagnostic_intervention_required is False
    assert saved2.repair_cap_reason == diagnostic.DIAGNOSTIC_BUDGET_SPENT_REASON
    assert invoked == []
    assert any(key.startswith("rung:") for key in ledger.read().ladder_evidence), (
        "cap exhaustion must reach a ladder rung recorded in the durable ledger"
    )

    # Attempt 3: already at cap; the typed marker preserves the counter
    # across loads and zero later invocations occur.
    assert not diagnostic.handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=root,
        record=saved2,
        failure="receipt unreadable 3",
        agents=(),
        invoke=custom_invoke,
        backend=backend,
        collector=collector,
        ledger=ledger,
        git=plumbing,
    )
    saved3 = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert saved3.merge_commit_repair_attempts == cap
    assert saved3.diagnostic_intervention_required is False
    assert invoked == []
