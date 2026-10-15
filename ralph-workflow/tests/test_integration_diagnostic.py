from pathlib import Path
from unittest.mock import MagicMock

import pytest

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.config.models import UnifiedConfig
from ralph.git.git_run_result import GitRunResult
from ralph.pipeline import run_loop
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    record_path,
    write_record,
)
from ralph.pipeline.operation_evidence import (
    EvidenceVerdict,
    InMemoryOperationEvidenceCollector,
    OperationEvidenceCollector,
    OperationFact,
    ownership_fingerprint,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope
from tests.test_auto_integrate_record_idempotent import _RecordBackend
from tests.test_pending_merge_commit_recovery import _commit, _git
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


@pytest.mark.subprocess_e2e
def test_positive_completion_proof_releases_evidence(tmp_path: Path) -> None:
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
        merge_commit_pending=True,
        owning_worktree=str(root),
    )
    record = record.model_copy(update={"diagnostic_ownership": ownership_fingerprint(record)})
    collector = InMemoryOperationEvidenceCollector(reachability={"MERGE_HEAD": True})
    diagnostic.set_operation_evidence_collector(collector)
    try:
        agent_called = False

        def fake_invoke(agent: str, prompt: Path) -> bool:
            nonlocal agent_called
            agent_called = True
            return False

        result = diagnostic.handoff_integration_diagnostic(
            root=root,
            record=record,
            failure="test failure",
            agents=("test-agent",),
            invoke=fake_invoke,
        )
        assert result is True
        assert not agent_called
        saved = read_record(root)
        assert saved is not None
        assert saved.diagnostic_evidence == ()
        assert saved.diagnostic_ownership is None
        assert saved.merge_commit_repair_attempts == 0
    finally:
        diagnostic.set_operation_evidence_collector(None)


@pytest.mark.subprocess_e2e
def test_unreachable_merge_object_takes_intervention_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    _commit(root, "file.txt", "content\n")

    gitdir = Path(_git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    nonexistent_sha = "0" * 40
    (gitdir / "MERGE_HEAD").write_text(nonexistent_sha + "\n", encoding="utf-8")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_merge=True,
        merge_commit_repair_attempts=2,
    )
    diagnostic.set_operation_evidence_collector(None)

    agent_called = False

    def fake_invoke(agent: str, prompt: Path) -> bool:
        nonlocal agent_called
        agent_called = True
        return False

    result = diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="test failure",
        agents=("test-agent",),
        invoke=fake_invoke,
    )
    assert result is False
    assert not agent_called
    saved = read_record(root)
    assert saved is not None
    assert saved.diagnostic_intervention_required is True
    assert saved.merge_commit_repair_attempts == 2
    assert (gitdir / "MERGE_HEAD").exists()
    assert (gitdir / "MERGE_HEAD").read_text(encoding="utf-8").strip() == nonexistent_sha


@pytest.mark.subprocess_e2e
def test_reachable_merge_head_with_unreadable_index_preserves_metadata(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0

    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature content\n")
    assert _git(root, "switch", "main").returncode == 0
    _commit(root, "shared.txt", "main content\n")

    assert _git(root, "merge", "--no-commit", "--no-ff", "feature").returncode != 0
    gitdir = Path(_git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    assert (gitdir / "MERGE_HEAD").exists()
    original_merge_head_bytes = (gitdir / "MERGE_HEAD").read_bytes()

    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=base,
        integrated_feature_sha=feature,
        merge_commit_parent=feature,
        merge_commit_pending=True,
        merge_commit_repair_attempts=1,
    )
    diagnostic.set_operation_evidence_collector(None)

    agent_called = False

    def fake_invoke(agent: str, prompt: Path) -> bool:
        nonlocal agent_called
        agent_called = True
        return False

    result = diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="test failure",
        agents=("test-agent",),
        invoke=fake_invoke,
    )
    assert result is False
    assert not agent_called
    saved = read_record(root)
    assert saved is not None
    assert saved.diagnostic_intervention_required is True
    assert saved.merge_commit_repair_attempts == 1
    assert (gitdir / "MERGE_HEAD").read_bytes() == original_merge_head_bytes


@pytest.mark.subprocess_e2e
def test_no_positive_proof_routes_to_intervention_without_consuming_attempt(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    _commit(root, "file.txt", "base\n")

    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        integrated_feature_sha="2" * 40,
        merge_commit_pending=True,
        merge_commit_repair_attempts=0,
    )
    diagnostic.set_operation_evidence_collector(None)

    agent_called = False

    def fake_invoke(agent: str, prompt: Path) -> bool:
        nonlocal agent_called
        agent_called = True
        return False

    result = diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="ancestry mismatch",
        agents=("test-agent",),
        invoke=fake_invoke,
    )
    assert result is False
    assert not agent_called
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


def test_ownership_proof_requires_nonempty_matching_worktree(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        owning_worktree=str(root),
    )
    record = record.model_copy(update={"diagnostic_ownership": ownership_fingerprint(record)})

    # Valid matching nonempty ownership
    ok, reason = diagnostic._check_ownership_proof(root, record)
    assert ok is True
    assert reason == "ownership verified"

    # Missing / empty owning_worktree
    no_wt = record.model_copy(update={"owning_worktree": None})
    ok, reason = diagnostic._check_ownership_proof(root, no_wt)
    assert ok is False
    assert "missing or empty" in reason

    empty_wt = record.model_copy(update={"owning_worktree": ""})
    ok, reason = diagnostic._check_ownership_proof(root, empty_wt)
    assert ok is False
    assert "missing or empty" in reason

    # Mismatched owning_worktree
    other = tmp_path / "other"
    other.mkdir()
    mismatched = record.model_copy(update={"owning_worktree": str(other)})
    ok, reason = diagnostic._check_ownership_proof(root, mismatched)
    assert ok is False
    assert "does not match" in reason

    # Missing diagnostic_ownership
    no_fingerprint = record.model_copy(update={"diagnostic_ownership": None})
    ok, reason = diagnostic._check_ownership_proof(root, no_fingerprint)
    assert ok is False
    assert "missing or empty" in reason

    # Forged diagnostic_ownership
    forged = record.model_copy(update={"diagnostic_ownership": "0" * 64})
    ok, reason = diagnostic._check_ownership_proof(root, forged)
    assert ok is False
    assert "does not match ownership_fingerprint" in reason


def test_index_tree_proof_treats_unknown_returncodes_and_exceptions_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    # 1. Unexpected exit code on MERGE_HEAD
    def mock_run_git_merge_head_error(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
    ) -> GitRunResult:
        args_t = tuple(args)
        if "MERGE_HEAD" in args_t:
            return GitRunResult(args=args_t, returncode=128, stdout="", stderr="fatal: lock error")
        return GitRunResult(args=args_t, returncode=1, stdout="", stderr="")

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", mock_run_git_merge_head_error)
    ok, reason = diagnostic._check_index_tree_proof(root)
    assert ok is False
    assert "MERGE_HEAD status is UNKNOWN (unexpected exit code 128)" in reason

    # 2. Exception on MERGE_HEAD
    def mock_run_git_merge_head_exc(*_args: object, **_kwargs: object) -> GitRunResult:
        raise RuntimeError("git merge-head subprocess crashed")

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", mock_run_git_merge_head_exc)
    ok, reason = diagnostic._check_index_tree_proof(root)
    assert ok is False
    assert "MERGE_HEAD status is UNKNOWN (git merge-head subprocess crashed)" in reason

    # 3. Unexpected exit code on REBASE_HEAD
    def mock_run_git_rebase_head_error(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
    ) -> GitRunResult:
        args_t = tuple(args)
        if "MERGE_HEAD" in args_t:
            return GitRunResult(args=args_t, returncode=1, stdout="", stderr="")
        if "REBASE_HEAD" in args_t:
            return GitRunResult(args=args_t, returncode=128, stdout="", stderr="fatal error")
        return GitRunResult(args=args_t, returncode=1, stdout="", stderr="")

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", mock_run_git_rebase_head_error)
    ok, reason = diagnostic._check_index_tree_proof(root)
    assert ok is False
    assert "REBASE_HEAD status is UNKNOWN (unexpected exit code 128)" in reason

    # 4. Exception on diff
    def mock_run_git_diff_exc(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
    ) -> GitRunResult:
        args_t = tuple(args)
        if "MERGE_HEAD" in args_t or "REBASE_HEAD" in args_t:
            return GitRunResult(args=args_t, returncode=1, stdout="", stderr="")
        if "--absolute-git-dir" in args_t:
            return GitRunResult(args=args_t, returncode=0, stdout=f"{root}\n", stderr="")
        if "diff" in args_t:
            raise OSError("diff command unavailable")
        return GitRunResult(args=args_t, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", mock_run_git_diff_exc)

    def fake_unmerged(_root: Path) -> list[str]:
        return []

    monkeypatch.setattr("ralph.git.merge.unmerged_paths", fake_unmerged)
    ok, reason = diagnostic._check_index_tree_proof(root)
    assert ok is False
    assert "git diff failed: diff command unavailable (UNKNOWN)" in reason


@pytest.mark.subprocess_e2e
def test_handoff_uncertain_marker_query_persists_intervention_preserving_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    base = _commit(root, "shared.txt", "base\n")
    second = _commit(root, "shared.txt", "second\n")

    gitdir = Path(_git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    marker_content = b"test-marker-sha\n"
    (gitdir / "MERGE_HEAD").write_bytes(marker_content)

    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha=base,
        integrated_feature_sha=second,
        merge_commit_parent=base,
        merge_commit_pending=True,
        merge_commit_repair_attempts=0,
        owning_worktree=str(root),
    )
    record = record.model_copy(update={"diagnostic_ownership": ownership_fingerprint(record)})

    from ralph.git.subprocess_runner import GitRunOptions
    from ralph.git.subprocess_runner import run_git as original_run_git

    def fake_run_git(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
        options: GitRunOptions | None = None,
        **kwargs: object,
    ) -> GitRunResult:
        args_t = tuple(args)
        if "MERGE_HEAD" in args_t and "check_merge_head" in label:
            return GitRunResult(args=args_t, returncode=128, stdout="", stderr="fatal lock error")
        return original_run_git(args, cwd=cwd, label=label, options=options)

    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", fake_run_git)

    agent_called = False

    def fake_invoke(agent: str, prompt: Path) -> bool:
        nonlocal agent_called
        agent_called = True
        return False

    result = diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="test uncertain error",
        agents=("test-agent",),
        invoke=fake_invoke,
    )
    assert result is False
    assert not agent_called
    saved = read_record(root)
    assert saved is not None
    assert saved.diagnostic_intervention_required is True
    assert "UNKNOWN" in (saved.repair_cap_reason or "")
    assert saved.merge_commit_repair_attempts == 0
    # Marker on disk preserved!
    assert (gitdir / "MERGE_HEAD").exists()
    assert (gitdir / "MERGE_HEAD").read_bytes() == marker_content


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


def test_diagnostic_evidence_verdict_closed_vocabulary() -> None:
    root = Path("/workspace")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    # 1. Empty evidence -> UNCHANGED
    assert diagnostic.diagnostic_evidence_verdict(root, record) == EvidenceVerdict.UNCHANGED

    # 2. Ownership mismatch -> CHANGED
    record_with_ownership = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "original"),),
            "diagnostic_ownership": "forged_ownership",
        }
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(root, record_with_ownership)
        == EvidenceVerdict.CHANGED
    )

    # 3. Unreadable observed fact -> UNKNOWN
    fingerprint = ownership_fingerprint(record)
    record_unreadable = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "unreadable"),),
            "diagnostic_ownership": fingerprint,
        }
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(root, record_unreadable) == EvidenceVerdict.UNKNOWN
    )

    # 4. Unreadable collector snapshot -> UNKNOWN
    record_valid = record.model_copy(
        update={
            "diagnostic_evidence": (("reflog", "valid_sha"),),
            "diagnostic_ownership": fingerprint,
        }
    )
    collector_unreadable = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="reflog", value="unreadable"),)
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(root, record_valid, collector=collector_unreadable)
        == EvidenceVerdict.UNKNOWN
    )

    # 5. Matching collector facts -> UNCHANGED
    collector_matching = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="reflog", value="valid_sha"),)
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(root, record_valid, collector=collector_matching)
        == EvidenceVerdict.UNCHANGED
    )

    # 6. Differing collector facts -> CHANGED
    collector_differing = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="reflog", value="different_sha"),)
    )
    assert (
        diagnostic.diagnostic_evidence_verdict(root, record_valid, collector=collector_differing)
        == EvidenceVerdict.CHANGED
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
    record = record.model_copy(update={"diagnostic_ownership": ownership_fingerprint(record)})
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="head", value="integrated"),)
    )

    # Evaluate verdict via release_verified_diagnostic
    verdict = diagnostic.release_verified_diagnostic(root, record, collector=collector)
    assert verdict == EvidenceVerdict.UNCHANGED

    # Ensure no record was written to disk by release_verified_diagnostic
    assert not record_path(root).exists()


def test_unknown_evidence_verdict_refuses_atomic_transition() -> None:
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        integrated_feature_sha="integrated",
        diagnostic_evidence=(("head", "unreadable"),),
    )
    record = record.model_copy(update={"diagnostic_ownership": ownership_fingerprint(record)})
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

    # Attempt 2: reaches cap, raises intervention
    assert not diagnostic.diagnose_retained_integration(
        ctx, "test failure 2", invoke=custom_invoke, collector=collector
    )
    s2 = read_record(root)
    assert s2 is not None and s2.merge_commit_repair_attempts == cap
    assert s2.diagnostic_intervention_required is True
    expected_reason = (
        "collector crashed" if use_raising_collector else "diagnostic repair exhausted"
    )
    assert expected_reason in (s2.repair_cap_reason or "")
    assert invoked == []

    # Attempt 3: already at cap with intervention; zero later invocations occur
    calls_before = getattr(collector, "calls", 0)
    assert not diagnostic.diagnose_retained_integration(
        ctx, "test failure 3", invoke=custom_invoke, collector=collector
    )
    s3 = read_record(root)
    assert s3 is not None and s3.merge_commit_repair_attempts == cap
    assert s3.diagnostic_intervention_required is True
    assert getattr(collector, "calls", 0) == calls_before
    assert invoked == []


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
