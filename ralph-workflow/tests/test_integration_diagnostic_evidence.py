from pathlib import Path

import pytest

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.git.git_run_result import GitRunResult
from ralph.pipeline import IntegrationAction
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

_GIT_NOT_A_REPOSITORY_RETURNCODE = 128


def _not_a_git_repository(
    args: tuple[str, ...] | list[str],
    **_kwargs: object,
) -> GitRunResult:
    return GitRunResult(
        args=tuple(args),
        returncode=_GIT_NOT_A_REPOSITORY_RETURNCODE,
        stdout="",
        stderr="fatal: not a git repository (or any of the parent directories): .git",
    )


def _in_memory_collector() -> InMemoryOperationEvidenceCollector:
    return InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )


def _wait_for_bounded_retry(
    _root: Path,
    *,
    record: object = None,
) -> IntegrationAction:
    return IntegrationAction.WAIT_FOR_BOUNDED_RETRY


def test_missing_evidence_rotates_inspection_without_clearing_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", _not_a_git_repository)
    monkeypatch.setattr(
        diagnostic,
        "decide_seam_action",
        _wait_for_bounded_retry,
    )

    backend = _RecordBackend()
    root = tmp_path
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
            collector=_in_memory_collector(),
        )
        record = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
        assert record.resolving_rebase and record.phase == "integrating"
        assert record.pre_feature_sha == "feature" and record.pre_target_sha == "target"
    assert agents == ["first", "first", "first", "second"]


@pytest.mark.parametrize("changed", ["reflog", "ownership"])
def test_diagnostic_changed_evidence_cannot_authorize_recovery(
    changed: str,
) -> None:
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
    held_fingerprint = identity_fingerprint(record)
    held_at = time.time()
    record = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": f"{held_fingerprint}:{held_at}",
        }
    )
    if changed == "ownership":
        record = record.model_copy(update={"target": "forged-target"})

    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="reflog", value="changed"),),
    )
    verdict = diagnostic.diagnostic_evidence_verdict(
        Path("/workspace"), record, collector=collector
    )
    if changed == "ownership":
        assert verdict == EvidenceVerdict.CHANGED
        assert not diagnostic.diagnostic_evidence_unchanged(
            Path("/workspace"), record, collector=collector
        )
    else:
        assert verdict == EvidenceVerdict.UNCHANGED
        assert diagnostic.diagnostic_evidence_unchanged(
            Path("/workspace"), record, collector=collector
        )


def test_diagnostic_evidence_verdict_closed_vocabulary() -> None:
    import time

    root = Path("/workspace")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    assert diagnostic.diagnostic_evidence_verdict(root, record) == EvidenceVerdict.UNCHANGED

    record_with_forged = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": "forged_ownership",
        }
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(root, record_with_forged) == EvidenceVerdict.UNKNOWN
    )

    fingerprint = identity_fingerprint(record)
    expired_held_at = time.time() - (diagnostic.EVIDENCE_LEASE_SECONDS + 100.0)
    record_expired = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": f"{fingerprint}:{expired_held_at}",
        }
    )
    assert diagnostic.diagnostic_evidence_verdict(root, record_expired) == EvidenceVerdict.UNKNOWN

    record_mismatch = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "valid"),),
            "diagnostic_ownership": f"wrong-fingerprint:{time.time()}",
        }
    )
    assert diagnostic.diagnostic_evidence_verdict(root, record_mismatch) == EvidenceVerdict.CHANGED

    record_valid = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "valid_sha"),),
            "diagnostic_ownership": f"{fingerprint}:{time.time()}",
        }
    )
    assert diagnostic.diagnostic_evidence_verdict(root, record_valid) == EvidenceVerdict.UNCHANGED

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

    verdict = diagnostic.release_verified_diagnostic(root, record, collector=collector)
    assert verdict == EvidenceVerdict.UNCHANGED

    assert not record_path(root).exists()


def test_unknown_evidence_verdict_refuses_atomic_transition() -> None:
    import time

    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        integrated_feature_sha="integrated",
        diagnostic_evidence=(("head", "unreadable"),),
    )
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
    plumbing = _FakePlumbing(changed=())

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
    root = Path("/workspace")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    record_malformed = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": "fingerprint:not-a-number",
        }
    )
    assert diagnostic.diagnostic_evidence_verdict(root, record_malformed) == EvidenceVerdict.UNKNOWN


def test_empty_evidence_with_unparseable_ownership_returns_changed() -> None:
    root = Path("/workspace")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    record_unparseable = record.model_copy(
        update={
            "diagnostic_evidence": (),
            "diagnostic_ownership": "forged_ownership_no_colon",
        }
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(root, record_unparseable) == EvidenceVerdict.UNKNOWN
    )
