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
    InMemoryOperationEvidenceCollector,
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
    record = record.model_copy(
        update={"diagnostic_ownership": ownership_fingerprint(record)}
    )
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
    record = record.model_copy(
        update={"diagnostic_ownership": ownership_fingerprint(record)}
    )

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
    record = record.model_copy(
        update={"diagnostic_ownership": ownership_fingerprint(record)}
    )

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
