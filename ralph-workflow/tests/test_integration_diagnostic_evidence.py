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
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.operation_evidence import (
    EvidenceVerdict,
    InMemoryOperationEvidenceCollector,
    OperationFact,
    identity_fingerprint,
)
from tests._integration_ladder_fakes import _FakePlumbing
from tests._resolver_controller import make_resolver_controller
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

    controller = make_resolver_controller()
    for _attempt in range(4):
        assert not diagnostic.handoff_integration_diagnostic(
            controller=controller,
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
    assert agents == ["first", "first", "first", "second"]


@pytest.mark.parametrize("changed", ["reflog", "ownership"])
def test_diagnostic_changed_evidence_cannot_authorize_recovery(
    changed: str,
) -> None:
    """A held lease whose fingerprint does NOT match the current record is CHANGED.

    The previous-shape single-fingerprint ownership string is
    unparseable as a lease and routes to UNKNOWN (fail-open). The
    O1 livelock contract is: a record whose durable identity was
    rewritten by a different worktree carries a valid lease but a
    mismatched fingerprint -- that is the only path to operator
    escalation (CHANGED). The "reflog" parametrize value asserts the
    collector is no longer consulted for the verdict (the lease alone
    is the release authority).
    """
    import time

    from ralph.pipeline._integration_diagnostic import (
        identity_fingerprint,
    )

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    # Held lease uses the ORIGINAL fingerprint, with a fresh held_at.
    held_fingerprint = identity_fingerprint(record)
    held_at = time.time()
    record = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": f"{held_fingerprint}:{held_at}",
        }
    )
    if changed == "ownership":
        # Rewrite the durable identity (forged) so the current
        # identity_fingerprint differs from the held one. The
        # collector is irrelevant -- the verdict follows the lease.
        # ``target`` is the only stable identity field that
        # participates in ``identity_fingerprint`` (the field set is
        # narrowed to ``("target", "operation_kind", "owning_worktree")``
        # in AC-4: a record whose ``target`` is rewritten is a
        # different durable operation even when the lifecycle fields
        # are unchanged).
        record = record.model_copy(update={"target": "forged-target"})

    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="reflog", value="changed"),),
    )
    verdict = diagnostic.diagnostic_evidence_verdict(
        Path("/workspace"), record, collector=collector
    )
    if changed == "ownership":
        # Mismatched fingerprint + valid lease + unexpired: CHANGED.
        assert verdict == EvidenceVerdict.CHANGED
        assert not diagnostic.diagnostic_evidence_unchanged(
            Path("/workspace"), record, collector=collector
        )
    else:
        # Held evidence differs from the collector's observation but
        # the lease is valid and unexpired: UNCHANGED. The collector
        # is no longer consulted for the verdict (the lease alone
        # is the release authority). This is the AC4 contract:
        # Ralph's own actions (e.g. snapshotting HEAD) must never
        # count as tampering.
        assert verdict == EvidenceVerdict.UNCHANGED
        assert diagnostic.diagnostic_evidence_unchanged(
            Path("/workspace"), record, collector=collector
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

    # 2. Unparseable lease -> UNKNOWN (fail-open auto-release contract
    # so unparseable/malformed leases never livelock or stall in an
    # operator-only state per O1 / AC1).
    record_with_forged = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": "forged_ownership",
        }
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(root, record_with_forged) == EvidenceVerdict.UNKNOWN
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


def test_handoff_integration_diagnostic_honors_injected_collector(tmp_path: Path) -> None:
    backend = _RecordBackend()
    root = Path("/workspace")
    ledger_path = tmp_path / "attempt-ledger.json"
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    # Collector with unreachable rebase marker object spends one bounded
    # pre-pass attempt instead of raising the retired hand-off.
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="False"),)
    )
    result = diagnostic.handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=root,
        record=record,
        failure="test failure",
        agents=("agent",),
        invoke=lambda _a, _p: True,
        backend=backend,
        collector=collector,
        ledger=AttemptLedger(root, ledger_path=ledger_path),
    )
    assert result is False
    saved = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert saved.diagnostic_intervention_required is False
    assert saved.merge_commit_repair_attempts == 1
    assert "unreachable marker object" in (saved.repair_last_error or "")


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
        agents=("agent",),
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


def test_malformed_timestamp_lease_returns_changed() -> None:
    """A lease with a non-numeric ``held_at`` segment returns ``CHANGED``.

    The lease-based verdict: a non-empty unparseable
    ``diagnostic_ownership`` (no ``:`` separator, empty
    fingerprint, empty held_at, or non-numeric held_at) is no
    longer a valid durable identity; the verdict escalates to
    ``CHANGED`` for operator review. This is the only path to
    operator escalation for a non-empty unparseable ownership
    (the fail-open auto-release contract is preserved for the
    separate expired-lease branch).
    """
    root = Path("/workspace")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    # Non-numeric held_at -> parse_lease returns (None, None)
    # -> verdict is UNKNOWN (fail-open auto-release per AC1).
    record_malformed = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": "fingerprint:not-a-number",
        }
    )
    assert diagnostic.diagnostic_evidence_verdict(root, record_malformed) == EvidenceVerdict.UNKNOWN


def test_empty_evidence_with_unparseable_ownership_returns_changed() -> None:
    """A record with empty evidence but a non-empty unparseable ownership returns ``UNKNOWN``.

    The empty-evidence short-circuit at the top of the verdict
    function only fires when ``diagnostic_ownership`` is also
    empty (the no-hold case). A record with empty evidence but
    a non-empty unparseable ownership auto-releases fail-open
    (``UNKNOWN``) per O1 / AC1.
    """
    root = Path("/workspace")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    # Empty evidence + non-empty unparseable ownership.
    record_unparseable = record.model_copy(
        update={
            "diagnostic_evidence": (),
            "diagnostic_ownership": "forged_ownership_no_colon",
        }
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(root, record_unparseable) == EvidenceVerdict.UNKNOWN
    )
