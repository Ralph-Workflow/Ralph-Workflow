from pathlib import Path
from unittest.mock import MagicMock

import pytest

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
    read_record,
    write_record,
)
from ralph.pipeline.operation_evidence import (
    InMemoryOperationEvidenceCollector,
    OperationEvidenceCollector,
    OperationFact,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope
from tests.test_pending_merge_commit_recovery import _commit, _git
from tests.test_pending_merge_repair import _retained_runtime_context


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
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_record.read_record_outcome",
        lambda _root, **_kw: RecordReadOutcome(
            disposition=RecordReadOutcomeDisposition.PRESENT, record=record
        ),
    )
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


@pytest.mark.subprocess_e2e
def test_public_diagnose_retained_integration_releases_on_four_positive_proofs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0

    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature\n")
    assert _git(root, "switch", "main").returncode == 0
    assert _git(root, "merge", "--no-ff", "-m", "merge commit", "feature").returncode == 0
    merge_sha = _git(root, "rev-parse", "HEAD").stdout.strip()

    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=base,
        integrated_feature_sha=merge_sha,
        merge_commit_parent=feature,
        merge_commit_pending=False,
        owning_worktree=str(root),
    )
    write_record(root, record)

    ctx = _build_diagnostic_ctx(root)
    result = diagnostic.diagnose_retained_integration(ctx, "test diagnostic failure")
    assert result is True

    saved = read_record(root)
    assert saved is not None
    assert saved.diagnostic_evidence == ()
    assert saved.diagnostic_ownership is None
    assert saved.diagnostic_intervention_required is False
    assert saved.merge_commit_repair_attempts == 0


@pytest.mark.subprocess_e2e
def test_public_diagnose_retained_integration_refuses_when_proof_missing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    base = _commit(root, "file.txt", "base\n")

    # Ancestry mismatch: record has nonexistent SHAs
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha=base,
        integrated_feature_sha="1" * 40,
        merge_commit_pending=True,
        merge_commit_repair_attempts=0,
        owning_worktree=str(root),
    )
    write_record(root, record)

    ctx = _build_diagnostic_ctx(root)
    result = diagnostic.diagnose_retained_integration(ctx, "ancestry mismatch test")
    assert result is False

    saved = read_record(root)
    assert saved is not None
    assert saved.diagnostic_intervention_required is True
    assert saved.merge_commit_repair_attempts == 0


def _build_diagnostic_ctx(root: Path) -> MagicMock:
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    ctx = MagicMock()
    ctx.workspace_scope = WorkspaceScope(root)
    ctx.config = UnifiedConfig()
    ctx.pipeline_deps = MagicMock()
    ctx.policy_bundle = bundle
    empty_registry: dict[str, object] = {}
    ctx.registry = empty_registry
    ctx.active_display = MagicMock()
    ctx.display_context = None
    return ctx


def test_diagnose_retained_integration_honors_injected_invoke_and_collector(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        integrated_feature_sha="2" * 40,
        merge_commit_pending=True,
        owning_worktree=str(root),
    )
    write_record(root, record)
    ctx = _build_diagnostic_ctx(root)
    invoked: list[str] = []

    def custom_invoke(agent: str, prompt: Path) -> bool:
        invoked.append(agent)
        return True

    custom_collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="False"),)
    )
    result = diagnostic.diagnose_retained_integration(
        ctx,
        failure="test",
        invoke=custom_invoke,
        collector=custom_collector,
    )
    assert result is False
    assert invoked == []
    saved = read_record(root)
    assert saved is not None
    assert saved.diagnostic_intervention_required is True


@pytest.mark.parametrize("use_raising_collector", [True, False])
def test_diagnose_retained_integration_repeated_failures_persist_intervention_at_cap(
    tmp_path: Path, use_raising_collector: bool
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cap = diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP

    class RaisingCollector(InMemoryOperationEvidenceCollector):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def snapshot(
            self,
            root: Path,
            record: IntegrationRecord,
            *,
            capture_repair_facts: bool | None = None,
            observed: tuple[OperationFact, ...] | None = None,
        ) -> tuple[OperationFact, ...]:
            self.calls += 1
            raise RuntimeError("collector crashed")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        resolving_rebase=True,
        merge_commit_repair_attempts=cap - 2,
        owning_worktree=str(root),
    )
    write_record(root, record)
    ctx = _build_diagnostic_ctx(root)
    invoked: list[str] = []

    def custom_invoke(agent: str, prompt: Path) -> bool:
        invoked.append(agent)
        return True

    collector: OperationEvidenceCollector
    if use_raising_collector:
        collector = RaisingCollector()
    else:
        ctx.registry = {}
        collector = InMemoryOperationEvidenceCollector(
            facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
        )

    # Attempt 1: from cap - 2 to cap - 1
    assert not diagnostic.diagnose_retained_integration(
        ctx, "test failure 1", invoke=custom_invoke, collector=collector
    )
    s1 = read_record(root)
    assert s1 is not None and s1.merge_commit_repair_attempts == cap - 1
    assert s1.diagnostic_intervention_required is False
    assert invoked == []

    # Attempt 2: reaches cap, raises intervention. The load-time
    # counter clamp rewrites any on-disk value >= cap down to cap-1
    # on the next read, so the test asserts the intervention flag
    # (the durable signal of exhaustion) rather than the literal
    # counter value.
    assert not diagnostic.diagnose_retained_integration(
        ctx, "test failure 2", invoke=custom_invoke, collector=collector
    )
    s2 = read_record(root)
    assert s2 is not None
    assert s2.merge_commit_repair_attempts == cap - 1  # load-time clamp
    assert s2.diagnostic_intervention_required is True
    expected_reason = (
        "collector crashed" if use_raising_collector else "diagnostic repair exhausted"
    )
    assert expected_reason in (s2.repair_cap_reason or "")
    assert invoked == []

    # Attempt 3: already at cap with intervention; zero later invocations occur.
    # The load-time counter clamp rewrites the on-disk >= cap value
    # to cap-1 on the next read, so the literal counter is no longer
    # the durable signal of exhaustion; the intervention flag is.
    calls_before = getattr(collector, "calls", 0)
    assert not diagnostic.diagnose_retained_integration(
        ctx, "test failure 3", invoke=custom_invoke, collector=collector
    )
    s3 = read_record(root)
    assert s3 is not None
    assert s3.merge_commit_repair_attempts == cap - 1  # load-time clamp
    assert s3.diagnostic_intervention_required is True
    assert getattr(collector, "calls", 0) == calls_before
    assert invoked == []
