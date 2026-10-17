"""Evidence-fingerprint / record-store branch of the integration diagnostic.

Split-out of ``test_integration_diagnostic.py`` to keep each file under the
1000-line cap enforced by ``ralph.testing.audit_repo_structure``. These tests
pin the lease/evidence contract on the diagnostic record store:

* rotating inspection agents when evidence is missing;
* the closed ``EvidenceVerdict`` vocabulary
  (``UNCHANGED`` / ``CHANGED`` / ``UNKNOWN``);
* the ``release_verified_diagnostic`` non-mutating verdict;
* the ``_check_atomic_transition_proof`` failure path on an expired lease;
* the injected-collector behaviour of ``handoff_integration_diagnostic``;
* the repeated-missing-agents cap on ``handoff_integration_diagnostic``.
"""

from pathlib import Path

import pytest

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    record_path,
)
from ralph.pipeline.operation_evidence import (
    EvidenceVerdict,
    InMemoryOperationEvidenceCollector,
    OperationFact,
    identity_fingerprint,
    ownership_fingerprint,
)
from tests.test_auto_integrate_record_idempotent import _RecordBackend


def test_missing_evidence_rotates_inspection_without_clearing_ownership() -> None:
    backend = _RecordBackend()
    root = Path("/workspace")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    agents: list[str] = []

    def inspect(agent: str, _prompt: Path) -> bool:
        agents.append(agent)
        return False

    for _attempt in range(2):
        assert not diagnostic.handoff_integration_diagnostic(
            root=root,
            record=record,
            failure="completion receipt unreadable",
            agents=("first", "second"),
            invoke=inspect,
            backend=backend,
        )
        record = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
        assert record.resolving_rebase and record.phase == "integrating"
        assert record.pre_feature_sha == "feature" and record.pre_target_sha == "target"
    assert agents == ["first", "second"]


@pytest.mark.parametrize("changed", ["reflog", "ownership"])
def test_diagnostic_changed_evidence_cannot_authorize_recovery(
    changed: str,
) -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    fingerprint = ownership_fingerprint(record)
    record = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": fingerprint,
        }
    )
    observed = "changed" if changed == "reflog" else "original"
    if changed == "ownership":
        record = record.model_copy(update={"pre_feature_sha": "forged"})

    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="reflog", value=observed),)
    )
    assert not diagnostic.diagnostic_evidence_unchanged(
        Path("/workspace"), record, collector=collector
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(Path("/workspace"), record, collector=collector)
        == EvidenceVerdict.CHANGED
    )


def test_diagnostic_evidence_verdict_closed_vocabulary() -> None:
    """Lease-based verdict: fingerprint match + held_at < lease window.

    The diagnostic no longer compares the evidence snapshot. The
    ``<fingerprint>:<held_at>`` lease in ``diagnostic_ownership`` is
    the sole release authority.
    """
    import time

    root = Path("/workspace")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    # 1. Empty evidence + empty ownership -> UNCHANGED (nothing to hold)
    assert diagnostic.diagnostic_evidence_verdict(root, record) == EvidenceVerdict.UNCHANGED

    # 2. Unparseable lease -> CHANGED
    record_with_forged = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": "forged_ownership",
        }
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(root, record_with_forged) == EvidenceVerdict.CHANGED
    )

    # 3. Expired lease (held_at far in the past) -> UNKNOWN
    fingerprint = identity_fingerprint(record)
    expired_held_at = time.time() - (diagnostic.EVIDENCE_LEASE_SECONDS + 100.0)
    record_expired = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": f"{fingerprint}:{expired_held_at}",
        }
    )
    assert diagnostic.diagnostic_evidence_verdict(root, record_expired) == EvidenceVerdict.UNKNOWN

    # 4. Mismatched fingerprint -> CHANGED
    record_mismatch = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "valid"),),
            "diagnostic_ownership": f"wrong-fingerprint:{time.time()}",
        }
    )
    assert diagnostic.diagnostic_evidence_verdict(root, record_mismatch) == EvidenceVerdict.CHANGED

    # 5. Valid fresh lease -> UNCHANGED (collector is irrelevant now)
    record_valid = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "valid_sha"),),
            "diagnostic_ownership": f"{fingerprint}:{time.time()}",
        }
    )
    assert diagnostic.diagnostic_evidence_verdict(root, record_valid) == EvidenceVerdict.UNCHANGED

    # 6. Differing collector facts -> UNCHANGED (collector no longer
    # consulted for the verdict; the lease alone is authoritative)
    collector_differing = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="reflog", value="different_sha"),)
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(root, record_valid, collector=collector_differing)
        == EvidenceVerdict.UNCHANGED
    )


def test_release_verified_diagnostic_returns_typed_verdict_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        integrated_feature_sha="integrated",
        diagnostic_evidence=(("head", "integrated"),),
    )
    record = record.model_copy(
        update={
            "diagnostic_ownership": f"{identity_fingerprint(record)}:{__import__('time').time()}"
        }
    )
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="head", value="integrated"),)
    )

    # Evaluate verdict via release_verified_diagnostic
    verdict = diagnostic.release_verified_diagnostic(root, record, collector=collector)
    assert verdict == EvidenceVerdict.UNCHANGED

    # Ensure no record was written to disk by release_verified_diagnostic
    assert not record_path(root).exists()


def test_unknown_evidence_verdict_refuses_atomic_transition() -> None:
    """An expired lease refuses the atomic transition (fail-open auto-release)."""
    import time

    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        integrated_feature_sha="integrated",
        diagnostic_evidence=(("head", "unreadable"),),
    )
    # Lease expired -> UNKNOWN -> atomic transition proof fails
    expired_held_at = time.time() - (diagnostic.EVIDENCE_LEASE_SECONDS + 100.0)
    record = record.model_copy(
        update={"diagnostic_ownership": f"{identity_fingerprint(record)}:{expired_held_at}"}
    )
    ok, reason = diagnostic._check_atomic_transition_proof(record, root=Path("/workspace"))
    assert ok is False
    assert "UNKNOWN" in reason


def test_handoff_integration_diagnostic_honors_injected_collector() -> None:
    backend = _RecordBackend()
    root = Path("/workspace")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    # Collector with unreachable rebase marker object should trigger intervention in pre-pass
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="False"),)
    )
    result = diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="test failure",
        agents=("agent",),
        invoke=lambda _a, _p: True,
        backend=backend,
        collector=collector,
    )
    assert result is False
    saved = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert saved.diagnostic_intervention_required is True
    assert "unreachable marker object" in (saved.repair_cap_reason or "")


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
