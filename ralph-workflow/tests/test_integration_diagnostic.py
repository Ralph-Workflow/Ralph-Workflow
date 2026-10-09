from pathlib import Path

import pytest

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.pipeline import run_loop
from ralph.pipeline.auto_integrate_record import IntegrationRecord, record_path
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from tests.test_auto_integrate_record_idempotent import _RecordBackend
from tests.test_pending_merge_repair import _retained_runtime_context


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


def test_runtime_missing_completion_evidence_reaches_diagnostic_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    retained = RebaseState(
        last_action="skipped",
        recovery_record_retained=True,
        last_reason="retained resolution lacks verified completion proof; work preserved and landing withheld",
    )
    failures: list[str] = []

    def read(_root: Path) -> IntegrationRecord:
        return record

    def startup(_ctx: run_loop._LoopContext, _state: RebaseState) -> RebaseState:
        return retained

    def save(_state: PipelineState, _ctx: run_loop._LoopContext) -> None:
        return None

    def inspect(_ctx: run_loop._LoopContext, failure: str) -> None:
        failures.append(failure)

    monkeypatch.setattr("ralph.pipeline.auto_integrate_record.read_record", read)
    monkeypatch.setattr(run_loop, "_run_startup_integration", startup)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", save)
    monkeypatch.setattr(
        run_loop,
        "_diagnose_retained_integration",
        inspect,
        raising=False,
    )
    blocked = run_loop._block_unresolved_integration(
        PipelineState(phase="planning", rebase=retained),
        _retained_runtime_context(),
        "planning",
    )
    assert blocked is not None and blocked[0].rebase.recovery_record_retained
    assert failures == [retained.last_reason]


@pytest.mark.parametrize("changed", ["reflog", "ownership"])
def test_diagnostic_changed_evidence_cannot_authorize_recovery(
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    fingerprint = diagnostic._ownership_fingerprint(record)
    record = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": fingerprint,
        }
    )
    observed = "changed" if changed == "reflog" else "original"
    if changed == "ownership":
        record = record.model_copy(update={"pre_feature_sha": "forged"})

    def snapshot(_root: Path) -> tuple[tuple[str, str], ...]:
        return (("reflog", observed),)

    monkeypatch.setattr(diagnostic, "_diagnostic_snapshot", snapshot)
    assert not diagnostic.diagnostic_evidence_unchanged(Path("/workspace"), record)


@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_pending_identity_unavailable_routes_inspection_instead_of_source_repair(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        merge_commit_pending=operation == "merge",
        rebase_continue_pending=operation == "rebase",
    )

    def identity(_root: Path, _record: IntegrationRecord) -> bool:
        return False

    monkeypatch.setattr(
        "ralph.pipeline._pending_merge_commit.pending_merge_identity_matches", identity
    )
    monkeypatch.setattr(
        "ralph.pipeline._pending_rebase_continue.pending_rebase_identity_matches", identity
    )
    assert diagnostic.diagnostic_needed(Path("/workspace"), record)
