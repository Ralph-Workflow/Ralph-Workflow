from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_recovery as recovery
from ralph.pipeline.auto_integrate import (
    IntegrationRecord,
    recover_incomplete_integration,
)
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit as _auto_integrate_after_commit,
)
from ralph.pipeline.auto_integrate_recovery import (
    TerminalStateViolationError,
    post_attempt_verify,
)
from ralph.pipeline.auto_integrate_transaction import LeaseOutcome
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


def auto_integrate_after_commit(
    config: UnifiedConfig,
    scope: WorkspaceScope,
    state: RebaseState,
) -> RebaseState | None:
    return _auto_integrate_after_commit(config, scope, state, sleep=_no_op, jitter=lambda: 0.0)


def _no_op(*_args: object, **_kwargs: object) -> None:
    return None


def _run(repo_root: Path, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    head_file = tmp_git_repo / ".git" / "HEAD"
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            return head_content.removeprefix("ref: refs/heads/")
    except OSError:
        pass
    from git import Repo

    return Repo(tmp_git_repo).active_branch.name


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    from git import Repo

    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo = Repo(repo_root)
    try:
        repo.index.add([filename])
        return repo.index.commit(message).hexsha
    finally:
        repo.close()


def _build_config(
    tmp_git_repo: Path,
    *,
    enabled: bool = True,
    target: str | None = None,
) -> UnifiedConfig:
    general: dict[str, object] = {"auto_integrate_enabled": enabled}
    if target is not None:
        general["auto_integrate_target"] = target
    return UnifiedConfig.model_validate({"general": general})


def test_recovery_mid_rebase_kill_preserves_feature_resolution(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    a_sha = _commit(tmp_git_repo, "shared.txt", "line1\nline2\nline3\n", "shared seed")
    _run(tmp_git_repo, "branch", "feature", a_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "line1\nFEATURE-D1\nline3\n", "feature: D1")
    pre_feature_sha = _commit(
        tmp_git_repo,
        "shared.txt",
        "line1\nFEATURE-D1\nFEATURE-D2\nline3\n",
        "feature: D2",
    )
    _run(tmp_git_repo, "checkout", base)
    pre_target_sha = _commit(tmp_git_repo, "shared.txt", "line1\nBASE\nline3\n", "base: B")
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0, "preflight: expected rebase to conflict"
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists(), (
        "preflight: expected rebase-apply/rebase-merge state on disk"
    )
    record = IntegrationRecord(
        phase="integrating",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")

    before = (tmp_git_repo / "shared.txt").read_bytes()
    head_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None and outcome.recovery_record_retained
    assert outcome.fast_forwarded is False
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists()
    assert not (git_dir / "MERGE_HEAD").exists()
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout == head_before
    assert (tmp_git_repo / "shared.txt").read_bytes() == before
    assert record_file.exists()


def test_recovery_killed_after_clean_rebase_before_ff(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    base_seed_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "feat_clean.txt", "feat clean\n", "feat clean")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base_clean.txt", "base clean\n", "base clean")
    _run(tmp_git_repo, "checkout", "feature")
    rebased = _run(tmp_git_repo, "rebase", base)
    assert rebased.returncode == 0, "test setup: rebase of feature onto base must succeed cleanly"
    pre_feature_sha = base_seed_sha
    pre_target_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    record = IntegrationRecord(
        phase="integrated",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
        integrated_feature_sha=_run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action in {"recovered", "skipped"}
    assert not record_file.exists()
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == feature_sha


def test_recovery_killed_after_clean_merge_before_ff(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    a_sha = _commit(tmp_git_repo, "shared.txt", "line1\nline2\nline3\n", "shared seed")
    _run(tmp_git_repo, "branch", "feature", a_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "line1\nFEATURE\nline3\n", "feature: D1")
    pre_feature_sha = _commit(
        tmp_git_repo, "shared.txt", "line1\nline2\nline3\n", "feature: D2 revert"
    )
    _run(tmp_git_repo, "checkout", base)
    pre_target_sha = _commit(tmp_git_repo, "shared.txt", "line1\nBASE\nline3\n", "base: B")
    _run(tmp_git_repo, "checkout", "feature")
    assert _run(tmp_git_repo, "merge", "--no-edit", base).returncode == 0
    record = IntegrationRecord(
        phase="integrated",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
        integrated_feature_sha=_run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action in {"recovered", "skipped"}
    assert not record_file.exists()


def test_recovery_no_record_preserves_operator_in_progress_rebase(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0, (
        "test setup: expected a real rebase conflict to leave state on disk"
    )
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists(), (
        "test setup: rebase-apply/rebase-merge must be on disk"
    )
    assert not (tmp_git_repo / ".agent" / "auto_integrate_in_progress.json").exists(), (
        "test setup: no auto-integrate record must exist"
    )
    before_head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    before_status = _run(tmp_git_repo, "status", "--porcelain").stdout
    before_porcelain_files = _run(
        tmp_git_repo, "diff", "--name-only", "--diff-filter=U"
    ).stdout.strip()
    before_rebase_apply_present = (git_dir / "rebase-apply").exists()
    before_rebase_merge_present = (git_dir / "rebase-merge").exists()

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is None, (
        "no auto-integrate record -> recovery must return None (operator"
        " in-progress rebase is owned by the operator, not auto-integrate)"
    )
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == before_head
    assert _run(tmp_git_repo, "status", "--porcelain").stdout == before_status
    assert (
        _run(tmp_git_repo, "diff", "--name-only", "--diff-filter=U").stdout.strip()
        == before_porcelain_files
    )
    assert (git_dir / "rebase-apply").exists() == before_rebase_apply_present
    assert (git_dir / "rebase-merge").exists() == before_rebase_merge_present
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists(), (
        "operator's in-progress rebase-apply/rebase-merge must still be on disk"
    )


def test_recovery_no_record_reclaims_stale_rebase_state_on_clean_tree(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0, (
        "test setup: expected a real rebase conflict to leave state on disk"
    )
    _run(tmp_git_repo, "reset", "--hard")
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists(), (
        "test setup: stale rebase state must be on disk"
    )
    for state_name in ("rebase-apply", "rebase-merge"):
        for identity in ("head-name", "onto"):
            (git_dir / state_name / identity).unlink(missing_ok=True)
    status = _run(tmp_git_repo, "status", "--porcelain", "--untracked-files=no")
    assert not status.stdout.strip(), "test setup: worktree must be clean"
    assert not (tmp_git_repo / ".agent" / "auto_integrate_in_progress.json").exists(), (
        "test setup: no auto-integrate record must exist"
    )

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))

    assert outcome is not None, (
        "stale unowned rebase state on a clean tree must be reclaimed, not"
        " silently ignored (PROMPT.md: noop is never an option)"
    )
    assert outcome.last_action == "recovered"
    assert not (git_dir / "rebase-apply").exists()
    assert not (git_dir / "rebase-merge").exists()
    assert not (git_dir / "REBASE_HEAD").exists()


def test_recovery_no_record_reclaims_stale_merge_state_on_clean_tree(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    conflicted = _run(tmp_git_repo, "merge", base)
    assert conflicted.returncode != 0, (
        "test setup: expected a real merge conflict to leave MERGE_HEAD"
    )
    _run(tmp_git_repo, "reset", "--hard")
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    (git_dir / "MERGE_HEAD").write_text("invalid-residual-parent\n", encoding="utf-8")
    (git_dir / "MERGE_MSG").unlink(missing_ok=True)
    status = _run(tmp_git_repo, "status", "--porcelain", "--untracked-files=no")
    assert not status.stdout.strip(), "test setup: worktree must be clean"
    assert not (tmp_git_repo / ".agent" / "auto_integrate_in_progress.json").exists(), (
        "test setup: no auto-integrate record must exist"
    )

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))

    assert outcome is not None, (
        "stale unowned MERGE_HEAD on a clean tree must be reclaimed, not"
        " silently ignored (terminal-state invariant)"
    )
    assert outcome.last_action == "recovered"
    assert not (git_dir / "MERGE_HEAD").exists()


def test_merge_exception_with_lingering_merge_head_retains_record(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph.pipeline import auto_integrate_rebase_merge as rm
    from ralph.pipeline.auto_integrate_record import (
        IntegrationRecord as Record,
    )
    from ralph.pipeline.auto_integrate_record import (
        read_record,
        write_record,
    )

    base = _base_branch(tmp_git_repo)
    head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    write_record(
        tmp_git_repo,
        Record(
            phase="integrating",
            target=base,
            pre_feature_sha=head,
            pre_target_sha=head,
        ),
    )
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    (git_dir / "MERGE_HEAD").write_text(head + "\n", encoding="utf-8")
    monkeypatch.setattr(rm, "endpoint_merge_with_resolution", _no_op)

    result = rm.run_rebase_or_merge(tmp_git_repo, base, None, prefer_merge=True)

    assert result.short_circuit is not None
    assert read_record(tmp_git_repo) is not None, (
        "record must be retained while MERGE_HEAD remains on disk --"
        " clearing it orphans the in-progress state recovery would own"
    )


def test_recovery_requires_config_before_retrying_initial_integration(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    pre_feature_sha = _commit(tmp_git_repo, "f.txt", "f\n", "f")
    pre_target_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    record = IntegrationRecord(
        phase="integrating",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.recovery_record_retained
    assert outcome.last_target == base
    assert outcome.fast_forwarded is False
    assert record_file.exists()
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == pre_feature_sha


def test_recovery_preserves_a_rebase_with_mismatched_ownership(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base v1\n", "base shared")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature v1\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base v2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists()
    pre_feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    pre_target_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    record = IntegrationRecord(
        phase="integrating",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    before = (tmp_git_repo / "shared.txt").read_bytes()
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.recovery_record_retained
    assert record_file.exists()
    assert (tmp_git_repo / "shared.txt").read_bytes() == before
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists()


def test_context_resolution_failure_returns_recorded_skip(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ralph.pipeline.auto_integrate as _ai_mod

    def _failing_probe(root: object) -> str | None:
        raise RuntimeError("probe failure")

    monkeypatch.setattr(_ai_mod, "_current_branch_or_detached_marker", _failing_probe)
    config = _build_config(tmp_git_repo, enabled=True)
    outcome = auto_integrate_after_commit(config, WorkspaceScope(tmp_git_repo), RebaseState())
    assert outcome is not None, (
        "AC-08 supplement: env lookup failure must produce a recorded skip,"
        " NOT a silent None (which would be indistinguishable from the AC-01"
        " disabled path)."
    )
    assert outcome.last_action == "skipped"
    assert "probe failure" in (outcome.last_reason or "")
    assert outcome.last_target is None
    assert outcome.fast_forwarded is False


def test_context_resolution_disabled_returns_none_not_skip(
    tmp_git_repo: Path,
) -> None:
    config = _build_config(tmp_git_repo, enabled=False)
    outcome = auto_integrate_after_commit(config, WorkspaceScope(tmp_git_repo), RebaseState())
    assert outcome is None, (
        "AC-01: disabled auto-integrate must return None (byte-identical"
        " no-op). A RebaseState here would mean we mutated run state when"
        " the feature is off."
    )


def test_recovery_treats_bogus_phase_record_as_corrupt(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base v1\n", "base shared")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature v1\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base v2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists()
    pre_feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    pre_target_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    import json

    payload: dict[str, str] = {
        "phase": "bogus",
        "target": base,
        "pre_feature_sha": pre_feature_sha,
        "pre_target_sha": pre_target_sha,
    }
    record_file.write_text(json.dumps(payload), encoding="utf-8")
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None and outcome.recovery_record_retained
    assert record_file.exists()
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists(), (
        "AC-11 supplement: corrupt record must NOT abort the operator's rebase"
    )
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == pre_feature_sha


@pytest.mark.parametrize(
    ("cause", "payload"),
    [
        ("corrupt_json", b"{not valid json"),
        ("unsupported_schema", b'{"totally_unknown_shape": [1, 2, 3]}'),
    ],
)
def test_unreadable_record_reports_ladder_escalation_not_retained_skip(
    tmp_git_repo: Path,
    cause: str,
    payload: bytes,
) -> None:
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_bytes(payload)
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_outcome.value == "ladder_escalation"
    assert outcome.last_decision == "block_for_recovery"
    assert outcome.operation_display_state == "evidence_diagnosis"
    assert cause in (outcome.last_reason or "")
    assert outcome.recovery_record_retained is True
    assert record_file.read_bytes() == payload


def test_unreadable_exists_permission_error_reports_ladder_escalation_not_generic_skip(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ralph.pipeline.auto_integrate_recovery as recovery_mod

    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    valid_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
    )
    record_file.write_text(valid_record.model_dump_json(), encoding="utf-8")
    original_bytes = record_file.read_bytes()

    from ralph.pipeline.auto_integrate_record import (
        RecordReadOutcome,
        RecordReadOutcomeDisposition,
    )

    def _unreadable_exists_outcome(
        workspace_root: object,
        *,
        backend: object = None,
        busy_attempt: object = None,
        busy_clock: object = None,
    ) -> RecordReadOutcome:
        return RecordReadOutcome(
            disposition=RecordReadOutcomeDisposition.UNREADABLE,
            unreadable_reason="backend_read_failure",
        )

    monkeypatch.setattr(
        recovery_mod,
        "read_record_outcome",
        _unreadable_exists_outcome,
    )

    class _AlwaysAcquired:
        def __enter__(self) -> LeaseOutcome:
            return LeaseOutcome.acquired_outcome()

        def __exit__(self, *args: object) -> bool:
            return False

    def _fake_transaction(root: object) -> _AlwaysAcquired:
        return _AlwaysAcquired()

    monkeypatch.setattr(recovery_mod, "integration_transaction", _fake_transaction)

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))

    assert outcome is not None
    assert outcome.last_outcome.value == "ladder_escalation"
    assert outcome.last_decision == "block_for_recovery"
    assert outcome.operation_display_state == "evidence_diagnosis"
    assert outcome.recovery_record_retained is True
    assert record_file.read_bytes() == original_bytes


def test_target_with_leading_dash_is_treated_as_ref_only(
    tmp_git_repo: Path,
) -> None:
    import ralph.git.rebase.rebase as _rebase_mod

    branch_name = "-dashy-target"
    _run(tmp_git_repo, "update-ref", f"refs/heads/{branch_name}", "HEAD")
    assert (
        _run(
            tmp_git_repo,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch_name}",
        ).returncode
        == 0
    )
    from ralph.git.rebase.rebase import RebaseNoOp, RebaseSuccess

    result = _rebase_mod.rebase_onto(branch_name, repo_root=tmp_git_repo)
    assert isinstance(result, (RebaseNoOp, RebaseSuccess)), (
        f"expected RebaseNoOp/RebaseSuccess for a dashed target treated as"
        f" a ref, got {type(result).__name__}"
    )
    assert isinstance(result, RebaseNoOp), (
        f"expected RebaseNoOp when HEAD == target; got {type(result).__name__}"
    )


def test_merge_target_with_leading_dash_is_treated_as_ref_only(
    tmp_git_repo: Path,
) -> None:
    import ralph.git.merge as _merge_mod

    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    feature_tip = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "checkout", _base_branch(tmp_git_repo))
    branch_name = "-dashy-merge-target"
    mainline_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "update-ref", f"refs/heads/{branch_name}", mainline_sha)
    _run(tmp_git_repo, "checkout", "feature")
    result = _merge_mod.merge_target_into_current(tmp_git_repo, branch_name)
    assert result.outcome in {"success", "noop"}
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_tip


def test_rebase_conflict_abort_failure_retains_record_for_recovery(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature\n", "feature")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "mainline\n", "mainline")
    _run(tmp_git_repo, "checkout", "feature")
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()

    import ralph.pipeline.auto_integrate_rebase_merge as auto_integrate_module

    def _failing_abort(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated normal-path abort failure")

    monkeypatch.setattr(auto_integrate_module, "abort_rebase_discarding_progress", _failing_abort)
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    with pytest.raises(TerminalStateViolationError) as excinfo:
        auto_integrate_after_commit(
            _build_config(tmp_git_repo, target=base),
            WorkspaceScope(tmp_git_repo),
            RebaseState(),
        )
    assert "REBASE_HEAD" in str(excinfo.value) or "rebase" in str(excinfo.value).lower(), (
        f"R6/AC-06: violation detail must name the leaked marker; got {excinfo.value!r}"
    )
    assert record_file.exists(), "abort failure must retain recovery ownership"
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists()

    before = (tmp_git_repo / "shared.txt").read_bytes()
    head_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout
    monkeypatch.undo()
    recovered = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert recovered is not None
    assert recovered.recovery_record_retained
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout == head_before
    assert (tmp_git_repo / "shared.txt").read_bytes() == before
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists()
    assert record_file.exists()


@pytest.mark.timeout_seconds(10)
def test_rebase_backup_ref_exists_during_attempt_and_is_cleaned_after(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "feature_only.txt", "feature-only content\n", "feature only")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "main_only.txt", "mainline-only content\n", "main only")
    _run(tmp_git_repo, "checkout", "feature")

    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.fast_forwarded is True, (
        f"B11/E5 happy-path setup failed: integration did not land; got outcome={outcome!r}"
    )

    backup_listing = _run(
        tmp_git_repo, "for-each-ref", "--format=%(refname)", "refs/rebase-backup/"
    )
    assert backup_listing.stdout.strip() == "", (
        "B11/E5: refs/rebase-backup/ must be empty after a verified land; "
        f"got {backup_listing.stdout!r}"
    )


def test_rebase_backup_ref_observed_mid_attempt_then_cleaned_after_land(
    tmp_git_repo: Path,
) -> None:
    from ralph.pipeline.auto_integrate import (
        _create_rebase_backup_ref,
        _delete_rebase_backup_ref,
    )

    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    pre_feature_sha = _commit(
        tmp_git_repo, "feature_only.txt", "feature-only content\n", "feature only"
    )

    backup_ref = _create_rebase_backup_ref(tmp_git_repo, pre_feature_sha, str(tmp_git_repo))
    assert backup_ref is not None, (
        "B11/E5: _create_rebase_backup_ref MUST return a non-None "
        "ref name when given a non-None pre-attempt SHA"
    )
    assert backup_ref.startswith("refs/rebase-backup/"), (
        f"B11/E5: backup ref name shape violation: {backup_ref!r}"
    )
    backup_sha = _run(tmp_git_repo, "rev-parse", backup_ref).stdout.strip()
    assert backup_sha == pre_feature_sha, (
        f"B11/E5: backup ref MUST resolve to the pre-attempt SHA "
        f"(got {backup_sha!r}, expected {pre_feature_sha!r})"
    )

    _delete_rebase_backup_ref(tmp_git_repo, backup_ref)
    after_delete = _run(
        tmp_git_repo, "for-each-ref", "--format=%(refname)", "refs/rebase-backup/"
    ).stdout.strip()
    assert after_delete == "", (
        f"B11/E5: _delete_rebase_backup_ref MUST remove the backup ref; got {after_delete!r}"
    )


def test_post_attempt_verify_clean_tree_after_land(tmp_git_repo: Path) -> None:
    from ralph.pipeline.auto_integrate_recovery import post_attempt_verify

    post_attempt_verify(tmp_git_repo, expected_head_sha=None, owns_resolution=False)


def test_post_attempt_verify_in_progress_marker_violation_raises_loudly(
    tmp_git_repo: Path,
) -> None:
    from ralph.pipeline._auto_integrate_recovery_git_state import rebase_bookkeeping_dir
    from ralph.pipeline.auto_integrate_recovery import (
        TerminalStateViolationError,
        post_attempt_verify,
    )

    git_dir = rebase_bookkeeping_dir(tmp_git_repo)
    assert git_dir is not None
    rebase_dir = git_dir / "rebase-merge"
    rebase_dir.mkdir()
    (rebase_dir / "head-name").write_text("refs/heads/feature\n")

    try:
        with pytest.raises(TerminalStateViolationError) as excinfo:
            post_attempt_verify(tmp_git_repo, expected_head_sha=None, owns_resolution=False)
        assert "rebase-merge" in str(excinfo.value), (
            f"R6/AC-06: violation detail must name the leaked marker; got {excinfo.value!r}"
        )
    finally:
        import shutil as _shutil

        if rebase_dir.exists():
            _shutil.rmtree(rebase_dir)


def test_post_attempt_verify_abort_path_restores_pre_attempt_sha(
    tmp_git_repo: Path,
) -> None:
    from ralph.pipeline.auto_integrate_recovery import (
        TerminalStateViolationError,
        post_attempt_verify,
    )

    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    pre_feature_sha = _commit(
        tmp_git_repo, "feature_only.txt", "feature-only content\n", "feature only"
    )
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "main_only.txt", "mainline-only content\n", "main only")
    _run(tmp_git_repo, "checkout", "feature")

    _commit(tmp_git_repo, "feature_extra.txt", "extra\n", "extra")

    with pytest.raises(TerminalStateViolationError) as excinfo:
        post_attempt_verify(
            tmp_git_repo,
            expected_head_sha=pre_feature_sha,
            owns_resolution=False,
        )
    assert "expected" in str(excinfo.value), (
        f"R6/AC-06: violation detail must name the SHA mismatch; got {excinfo.value!r}"
    )


@pytest.mark.timeout_seconds(20)
def test_integrate_once_propagates_terminal_violation_on_exception_path(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph.pipeline import auto_integrate as auto_integrate_module

    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    seed_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "feature_only.txt", "feature\n", "feature only")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base_only.txt", "base\n", "base only")
    _run(tmp_git_repo, "checkout", "feature")

    try:
        git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
        if not git_dir.is_absolute():
            git_dir = (tmp_git_repo / git_dir).resolve()

        def _plant_marker() -> None:
            rebase_dir = git_dir / "rebase-merge"
            rebase_dir.mkdir()
            (rebase_dir / "head-name").write_text("refs/heads/feature\n")
            (rebase_dir / "onto").write_text(f"refs/heads/{base}\n")

        called = [False]

        def _check_with_marker(_root: Path) -> None:
            if not called[0]:
                called[0] = True
                _plant_marker()

        monkeypatch.setattr(auto_integrate_module, "check_rebase_preconditions", _check_with_marker)

        with pytest.raises(TerminalStateViolationError):
            auto_integrate_after_commit(
                _build_config(tmp_git_repo, target=base),
                WorkspaceScope(tmp_git_repo),
                RebaseState(),
            )
    finally:
        import shutil as _shutil

        rebase_dir = git_dir / "rebase-merge"
        if rebase_dir.exists():
            _shutil.rmtree(rebase_dir)
        record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
        if record_file.exists():
            record_file.unlink()
        backup_dir = git_dir / "refs"
        if backup_dir.exists():
            for ref in _run(
                tmp_git_repo,
                "for-each-ref",
                "--format=%(refname)",
                "refs/rebase-backup/",
            ).stdout.splitlines():
                _run(tmp_git_repo, "update-ref", "-d", ref.strip())


@pytest.mark.timeout_seconds(20)
def test_recovery_honors_target_reclaim_opt_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        integrated_feature_sha="c" * 40,
    )

    def refused(*_args: object, **kwargs: object) -> tuple[bool, str]:
        return False, str(kwargs["reclaim_target_worktree"])

    from ralph.pipeline import _recovery_fast_forward_landing as landing

    monkeypatch.setattr(landing, "fast_forward_target", refused)
    monkeypatch.setattr(landing, "post_attempt_verify", _no_op)
    monkeypatch.setattr(landing, "_delete_rebase_backup_refs", _no_op)

    outcome = recovery._land_and_reconcile(
        tmp_path,
        record,
        "c" * 40,
        None,
        UnifiedConfig.model_validate(
            {"general": {"auto_integrate_reclaim_target_worktree": False}}
        ),
    )

    assert outcome.recovery_record_retained is True
    assert outcome.last_reason is not None
    assert "False" in outcome.last_reason


def test_seam_level_reclaim_lands_integration_without_recovery_preamble(
    tmp_git_repo: Path,
) -> None:
    from ralph.git.rebase import check_rebase_preconditions

    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "feature_only.txt", "feature a\n", "feature a")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0, (
        "test setup: expected a real rebase conflict to leave state on disk"
    )
    pre_rebase_feature_sha = _run(tmp_git_repo, "rev-parse", "feature").stdout.strip()
    _run(tmp_git_repo, "reset", "--hard", pre_rebase_feature_sha)
    _run(tmp_git_repo, "checkout", "feature")
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists(), (
        "test setup: stale rebase state must be on disk"
    )
    for state_name in ("rebase-apply", "rebase-merge"):
        for identity in ("head-name", "onto"):
            (git_dir / state_name / identity).unlink(missing_ok=True)
    status = _run(tmp_git_repo, "status", "--porcelain", "--untracked-files=no")
    assert not status.stdout.strip(), "test setup: worktree must be clean"
    assert not (tmp_git_repo / ".agent" / "auto_integrate_in_progress.json").exists(), (
        "test setup: no auto-integrate record must exist"
    )

    result = auto_integrate_after_commit(
        _build_config(tmp_git_repo, target=base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )
    assert result is not None, (
        "AC-07/R8: the seam-level integration must surface a recorded "
        "outcome (the auto-integrate feature is enabled, so a silent "
        "None is the silent-noop bug)"
    )
    assert not (result.last_reason or "").startswith("preconditions not met"), (
        "AC-07/R8: seam-level reclaim must have removed the "
        "stale state and let the integration proceed; the "
        "silent-noop bug recorded a 'preconditions not met' "
        "skip here. Got: "
        f"{result.last_reason!r}"
    )
    assert not (git_dir / "rebase-apply").exists(), (
        "AC-07/R8: seam-level reclaim must have removed the rebase-apply marker in the same seam"
    )
    assert not (git_dir / "rebase-merge").exists(), (
        "AC-07/R8: seam-level reclaim must have removed the rebase-merge marker in the same seam"
    )
    check_rebase_preconditions(tmp_git_repo)


@pytest.mark.timeout_seconds(20)
def test_seam_level_reclaim_preserves_dirty_tree(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    base_seed = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0, (
        "test setup: expected a real rebase conflict to leave state on disk"
    )
    _run(tmp_git_repo, "symbolic-ref", "HEAD", "refs/heads/feature")
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists(), (
        "test setup: stale rebase state must be on disk"
    )
    status = _run(tmp_git_repo, "status", "--porcelain", "--untracked-files=no")
    assert status.stdout.strip(), "test setup: worktree must be dirty with unmerged paths"

    result = auto_integrate_after_commit(
        _build_config(tmp_git_repo, target=base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )
    assert result is not None, (
        "AC-11 case 4: a dirty tree with unmerged paths is "
        "operator-owned and MUST be preserved; the seam-level "
        "reclaim must NOT touch it. The integration must "
        "surface a recorded skip in this case, never a "
        "silent None."
    )
    assert (result.last_reason or "").startswith("preconditions not met"), (
        "AC-11 case 4: a dirty tree with unmerged paths must "
        "trigger the precondition-failure skip; the recorded "
        "reason names the precondition that blocked the "
        "integration. Got: "
        f"{result.last_reason!r}"
    )
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists(), (
        "AC-11 case 4: dirty-tree reclaim MUST preserve the operator-owned in-progress rebase state"
    )


def test_post_attempt_verify_passes_on_bare_rebase_head_with_no_active_rebase_dir(
    tmp_git_repo: Path,
) -> None:
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    (git_dir / "REBASE_HEAD").write_text("0" * 40, encoding="utf-8")

    post_attempt_verify(
        tmp_git_repo,
        expected_head_sha=None,
        owns_resolution=False,
    )


def test_post_attempt_verify_still_raises_when_rebase_head_accompanies_an_active_rebase_dir(
    tmp_git_repo: Path,
) -> None:
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    (git_dir / "REBASE_HEAD").write_text("0" * 40, encoding="utf-8")
    rebase_dir = git_dir / "rebase-merge"
    rebase_dir.mkdir()
    (rebase_dir / "head-name").write_text("refs/heads/feature", encoding="utf-8")

    with pytest.raises(TerminalStateViolationError, match=r"REBASE_HEAD|rebase-merge"):
        post_attempt_verify(
            tmp_git_repo,
            expected_head_sha=None,
            owns_resolution=False,
        )
