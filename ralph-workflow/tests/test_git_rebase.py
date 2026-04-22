"""Behavioral tests for git rebase helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import GitCommandError, Repo

from ralph.git.rebase.rebase import (
    ProcessExecutor,
    ProcessResult,
    RebaseConflicts,
    RebaseNoOp,
    RebaseOperationError,
    SubprocessExecutor,
    abort_rebase,
    continue_rebase,
    get_conflicted_files,
    rebase_onto,
)
from ralph.process.manager import ProcessStatus, get_process_manager, reset_process_manager

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
from ralph.git.rebase.rebase_continuation import (
    ConflictRemainingError,
    NoRebaseInProgressError,
    RebaseVerificationError,
    continue_rebase_at,
    rebase_in_progress_at,
    verify_rebase_completed_at,
)
from ralph.git.rebase.rebase_kinds import RebaseKind, classify_rebase_error
from ralph.git.rebase.rebase_preconditions import (
    RebasePreconditionError,
    check_rebase_preconditions,
)
from ralph.git.rebase.rebase_state_machine import (
    InvalidTransitionError,
    RebaseEvent,
    RebaseStateMachine,
    RecoveryAction,
)


class FakeProcessExecutor(ProcessExecutor):
    def __init__(self, responses: Mapping[tuple[str, tuple[str, ...]], ProcessResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def execute(
        self,
        command: str,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        key = (command, tuple(args))
        self.calls.append(key)
        return self.responses.get(key, ProcessResult(returncode=0, stdout="", stderr=""))


def _mk_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> ProcessResult:
    return ProcessResult(returncode=returncode, stdout=stdout, stderr=stderr)


def _create_rebase_state(repo_root: Path) -> None:
    git_dir = Path(repo_root, ".git")
    (git_dir / "rebase-apply").mkdir(parents=True, exist_ok=True)


def test_abort_rebase_requires_rebase_state(tmp_git_repo: Path) -> None:
    with pytest.raises(RebaseOperationError, match="rebase in progress"):
        abort_rebase(repo_root=tmp_git_repo)


def test_abort_rebase_invokes_git_when_rebase_in_progress(tmp_git_repo: Path) -> None:
    _create_rebase_state(tmp_git_repo)
    responses = {
        ("git", ("rebase", "--abort")): _mk_result(),
    }
    executor = FakeProcessExecutor(responses)

    abort_rebase(repo_root=tmp_git_repo, executor=executor)

    assert executor.calls == [("git", ("rebase", "--abort"))]


def test_continue_rebase_requires_conflicts_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    _create_rebase_state(tmp_git_repo)
    monkeypatch.setattr(
        "ralph.git.rebase.rebase.get_conflicted_files",
        lambda repo_root, executor=None: ["README.md"],
    )

    executor = FakeProcessExecutor({})

    with pytest.raises(RebaseOperationError, match="Conflicts remain"):
        continue_rebase(repo_root=tmp_git_repo, executor=executor)


def test_continue_rebase_executes_cli_when_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    _create_rebase_state(tmp_git_repo)
    monkeypatch.setattr(
        "ralph.git.rebase.rebase.get_conflicted_files",
        lambda repo_root, executor=None: [],
    )
    responses = {
        ("git", ("rebase", "--continue")): _mk_result(),
    }
    executor = FakeProcessExecutor(responses)

    continue_rebase(repo_root=tmp_git_repo, executor=executor)

    assert executor.calls == [("git", ("rebase", "--continue"))]


def test_rebase_onto_returns_noop_when_branch_up_to_date(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    branch_name = "feature-noop"
    repo.git.checkout("-b", branch_name)
    responses = {
        ("git", ("merge-base", "--is-ancestor", branch_name, "HEAD")): _mk_result(returncode=0),
    }
    executor = FakeProcessExecutor(responses)

    result = rebase_onto(upstream_branch=branch_name, repo_root=tmp_git_repo, executor=executor)

    assert isinstance(result, RebaseNoOp)
    assert "up-to-date" in result.reason
    assert executor.calls == [
        ("git", ("merge-base", "--is-ancestor", branch_name, "HEAD")),
    ]


def test_rebase_onto_detects_conflicts(monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    current = repo.active_branch.name
    base_branch = current
    repo.git.checkout("-b", "feature-conflict")
    responses = {
        ("git", ("merge-base", "--is-ancestor", base_branch, "HEAD")): _mk_result(returncode=1),
        ("git", ("rebase", base_branch)): _mk_result(
            returncode=1,
            stderr="CONFLICT (content): Merge conflict in README.md",
        ),
    }
    executor = FakeProcessExecutor(responses)
    monkeypatch.setattr(
        "ralph.git.rebase.rebase.get_conflicted_files",
        lambda repo_root, executor=None: ["README.md"],
    )

    result = rebase_onto(upstream_branch=base_branch, repo_root=tmp_git_repo, executor=executor)

    assert isinstance(result, RebaseConflicts)
    assert result.files == ["README.md"]


def test_get_conflicted_files_reports_conflicts(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    (tmp_git_repo / "README.md").write_text("feature content")
    repo.index.add(["README.md"])
    repo.index.commit("feature update")
    repo.git.checkout(base)
    (tmp_git_repo / "README.md").write_text("base content")
    repo.index.add(["README.md"])
    repo.index.commit("base update")
    repo.git.checkout("feature")
    with pytest.raises(GitCommandError):
        repo.git.merge(base)

    try:
        files = get_conflicted_files(repo_root=tmp_git_repo)
        assert "README.md" in files
    finally:
        repo.git.merge("--abort")


def test_classify_rebase_error_detects_interactive_stop_command() -> None:
    stderr = "Stopped at deadbeef... edit command\n"

    result = classify_rebase_error(stderr, "")

    assert result.kind == RebaseKind.INTERACTIVE_STOP
    assert result.metadata["command"] == "edit"


def test_classify_rebase_error_detects_reference_update_failure() -> None:
    stderr = "fatal: cannot lock ref 'refs/heads/main': is at abc123 but expected def456\n"

    result = classify_rebase_error(stderr, "")

    assert result.kind == RebaseKind.REFERENCE_UPDATE_FAILED
    details = result.metadata["details"]
    assert isinstance(details, str)
    assert "cannot lock ref" in details


def test_check_rebase_preconditions_requires_both_identity_fields(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    writer = repo.config_writer()
    writer.set_value("user", "name", "Example User")
    writer.set_value("user", "email", "")
    writer.release()

    with pytest.raises(RebasePreconditionError, match="Git identity is not configured"):
        check_rebase_preconditions(tmp_git_repo)


def test_check_rebase_preconditions_detects_sparse_checkout_without_patterns(
    tmp_git_repo: Path,
) -> None:
    repo = Repo(tmp_git_repo)
    writer = repo.config_writer()
    writer.set_value("core", "sparseCheckout", "true")
    writer.release()

    info_dir = tmp_git_repo / ".git" / "info"
    info_dir.mkdir(exist_ok=True)
    (info_dir / "sparse-checkout").write_text("")

    with pytest.raises(RebasePreconditionError, match="Sparse checkout configuration is empty"):
        check_rebase_preconditions(tmp_git_repo)


def test_state_machine_honors_custom_max_recovery_attempts() -> None:
    machine = RebaseStateMachine.new("main", persist=False, max_recovery_attempts=1)

    machine.record_error("boom")

    assert machine.should_abort()
    assert not machine.can_recover()


def test_state_machine_apply_event_requires_file_for_conflict_transitions() -> None:
    machine = RebaseStateMachine.new("main", persist=False)
    machine.apply_event(RebaseEvent.START_REBASE)

    with pytest.raises(InvalidTransitionError, match="requires a file"):
        machine.apply_event(RebaseEvent.CONFLICT_DETECTED)


def test_recovery_action_prefers_abort_once_attempt_limit_reached() -> None:
    action = RecoveryAction.decide(
        classify_rebase_error("CONFLICT (content): Merge conflict in app.py", ""),
        error_count=2,
        max_attempts=2,
    )

    assert action is RecoveryAction.Abort


def test_continue_rebase_at_requires_active_rebase(tmp_git_repo: Path) -> None:
    with pytest.raises(NoRebaseInProgressError):
        continue_rebase_at(tmp_git_repo)


def test_continue_rebase_at_blocks_when_index_has_conflicts(tmp_git_repo: Path) -> None:
    base_branch = _setup_conflicted_rebase(tmp_git_repo)

    with pytest.raises(ConflictRemainingError, match="Conflicts still exist"):
        continue_rebase_at(tmp_git_repo)

    assert rebase_in_progress_at(tmp_git_repo)
    assert not verify_rebase_completed_at(tmp_git_repo, base_branch)


def test_verify_rebase_completed_at_rejects_detached_head(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    repo.git.checkout(repo.head.commit.hexsha)

    with pytest.raises(RebaseVerificationError, match="HEAD is detached"):
        verify_rebase_completed_at(tmp_git_repo, "main")


def _assert_full_lifecycle(events: list, label_prefix: str) -> None:
    """Assert each PID with the given label prefix emitted SPAWNED->RUNNING->EXITED."""
    labeled = [
        e for e in events if e.record.label and e.record.label.startswith(label_prefix)
    ]
    assert labeled, f"Expected events with label prefix '{label_prefix}'"

    pids = dict.fromkeys(e.record.pid for e in labeled)
    assert pids, f"Expected at least one tracked spawn with label prefix '{label_prefix}'"

    for pid in pids:
        pid_events = [e for e in labeled if e.record.pid == pid]
        transitions = [(e.previous_status, e.new_status) for e in pid_events]
        assert (ProcessStatus.SPAWNED, ProcessStatus.RUNNING) in transitions, (
            f"Process {pid} (label {label_prefix!r}) missing SPAWNED->RUNNING; "
            f"got {transitions}"
        )
        assert (ProcessStatus.RUNNING, ProcessStatus.EXITED) in transitions, (
            f"Process {pid} (label {label_prefix!r}) missing RUNNING->EXITED; "
            f"got {transitions}"
        )


def test_subprocess_executor_emits_process_manager_events(tmp_git_repo: Path) -> None:
    """Real SubprocessExecutor routes git calls through ProcessManager with full lifecycle."""
    reset_process_manager()
    events: list = []
    unsubscribe = get_process_manager().register_listener(events.append)

    try:
        executor = SubprocessExecutor()
        get_conflicted_files(repo_root=tmp_git_repo, executor=executor)
    finally:
        unsubscribe()
        reset_process_manager()

    _assert_full_lifecycle(events, "git-rebase:")


def _setup_conflicted_rebase(repo_root: Path, feature_branch: str = "feature") -> str:
    repo = Repo(repo_root)
    base_branch = repo.active_branch.name
    conflict_file = repo_root / "conflict.txt"

    conflict_file.write_text("base\n")
    repo.index.add(["conflict.txt"])
    repo.index.commit("add conflict file")

    repo.git.checkout("-b", feature_branch)
    conflict_file.write_text("feature\n")
    repo.index.add(["conflict.txt"])
    repo.index.commit("feature change")

    repo.git.checkout(base_branch)
    conflict_file.write_text("main\n")
    repo.index.add(["conflict.txt"])
    repo.index.commit("main change")

    repo.git.checkout(feature_branch)
    result = subprocess.run(
        ["git", "rebase", base_branch],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0, "Expected the rebase command to conflict"

    return base_branch
