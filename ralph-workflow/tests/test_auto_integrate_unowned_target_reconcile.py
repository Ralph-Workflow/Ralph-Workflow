from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.git import remote_push as remote_push_module
from ralph.git.git_run_result import GitRunResult
from ralph.git.rebase.rebase import RebaseConflicts, RebaseSuccess
from ralph.pipeline import auto_integrate_remote_reconcile as remote_reconcile
from ralph.pipeline.auto_integrate_ff import RemoteClient
from ralph.pipeline.auto_integrate_remote_sync import reconcile_after_rejected_push
from ralph.pipeline.auto_integrate_sync import REFRESH_DIVERGED
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from tests._scenario_harness import commit_file, git

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]


def _unowned_diverged_target(root: Path, tmp_path: Path) -> tuple[Path, str]:
    commit_file(root, "shared.txt", "base\n", "base")
    git(root, "checkout", "-q", "-b", "remote-main")
    remote_tip = commit_file(root, "shared.txt", "remote\n", "remote main")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "local\n", "local main")
    git(root, "update-ref", "refs/heads/tacos/release/tacos", remote_tip)
    git(root, "branch", "release/tacos", "main")
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "feature.txt", "feature\n", "feature")
    primary = tmp_path / "primary-main"
    git(root, "worktree", "add", "-q", str(primary), "main")
    return primary, remote_tip


def test_unowned_target_reconciles_via_temporary_workspace_and_cleans_up(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    primary_root, remote_tip = _unowned_diverged_target(tmp_git_repo, tmp_path)
    feature_tip = git(tmp_git_repo, "rev-parse", "HEAD")
    git(tmp_git_repo, "worktree", "remove", "--force", str(primary_root))

    def resolve(root: Path, _target: str, stop: object) -> bool:
        conflicted_files = getattr(stop, "conflicted_files", ())
        for relative in conflicted_files:
            (root / relative).write_text("resolved remote and local\n", encoding="utf-8")
        return True

    outcome = remote_reconcile.reconcile_target_onto_remote(
        tmp_git_repo,
        "release/tacos",
        "tacos",
        rebase_stop_resolver=resolve,
    )

    assert outcome.reconciled is True
    assert outcome.reason == ""
    assert git(tmp_git_repo, "rev-parse", "release/tacos") != remote_tip
    assert git(tmp_git_repo, "rev-parse", "HEAD") == feature_tip
    worktrees = git(tmp_git_repo, "worktree", "list", "--porcelain")
    assert "ralph-reconcile-release_tacos-" not in worktrees


def test_unowned_target_interruption_cleans_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "feature-wt"
    repo_root.mkdir()
    (repo_root / "dirty.txt").write_text("uncommitted feature work\n", encoding="utf-8")

    primary_root = tmp_path / "main-repo"
    primary_root.mkdir()

    monkeypatch.setattr(remote_reconcile, "find_main_worktree_root", lambda _r: primary_root)
    monkeypatch.setattr(
        remote_reconcile, "worktree_lookup", lambda _r, _t: ("not_checked_out", None)
    )

    def fake_branch_sha(_root: Path, ref: str) -> str:
        if ref == "release/tacos":
            return "local-sha"
        if ref == "tacos/release/tacos":
            return "remote-sha"
        return "some-sha"

    monkeypatch.setattr(remote_reconcile, "branch_sha", fake_branch_sha)
    monkeypatch.setattr(remote_reconcile, "get_head_sha", lambda _root: "completed-feature")
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(remote_reconcile, "read_record", lambda *_a: None)
    cleared_records: list[Path] = []
    monkeypatch.setattr(remote_reconcile, "clear_record", cleared_records.append)

    git_cmds: list[tuple[str, ...]] = []

    def fake_run_git(cmd: tuple[str, ...], cwd: Path | None = None, **_kw: object) -> GitRunResult:
        git_cmds.append(cmd)
        if cmd[:2] == ("worktree", "add"):
            wt_path = Path(cmd[2])
            wt_path.mkdir(parents=True, exist_ok=True)
            return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:2] == ("worktree", "remove"):
            wt_path = Path(cmd[-1])
            shutil.rmtree(wt_path, ignore_errors=True)
            return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")
        return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_reconcile, "run_git", fake_run_git)
    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", fake_run_git)
    monkeypatch.setattr("ralph.pipeline.conflict_resolution.rebase_loop.run_git", fake_run_git)

    monkeypatch.setattr(
        remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseConflicts(["conflict"])
    )
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: True)
    aborted: list[Path] = []
    monkeypatch.setattr(remote_reconcile, "abort_rebase_discarding_progress", aborted.append)

    def failing_resolver(_root: Path, _target: str, _stop: object) -> bool:
        raise RuntimeError("simulated interruption")

    outcome = remote_reconcile.reconcile_target_onto_remote(
        repo_root,
        "release/tacos",
        "tacos",
        rebase_stop_resolver=failing_resolver,
    )

    assert outcome.reconciled is False
    assert outcome.cleanly_aborted is True
    assert "cleanly aborted conflict" in outcome.reason
    assert (
        "resolution interrupted" in outcome.reason
        or "conflict resolution incomplete" in outcome.reason
    )
    assert "retained for recovery" not in outcome.reason
    assert len(aborted) >= 1
    remove_cmds = [c for c in git_cmds if c[:2] == ("worktree", "remove")]
    assert len(remove_cmds) == 1
    assert (repo_root / "dirty.txt").read_text(encoding="utf-8") == "uncommitted feature work\n"
    assert repo_root in cleared_records


def test_unowned_target_recovery_from_stale_temporary_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "feature-wt"
    repo_root.mkdir()
    primary_root = tmp_path / "main-repo"
    primary_root.mkdir()

    stale_wt = tmp_path / "ralph-reconcile-release_tacos-stale123"
    stale_wt.mkdir()

    lookup_counts = 0

    def fake_worktree_lookup(_root: Path, _target: str) -> tuple[str, Path | None]:
        nonlocal lookup_counts
        lookup_counts += 1
        if lookup_counts == 1:
            return ("found", stale_wt)
        return ("not_checked_out", None)

    monkeypatch.setattr(remote_reconcile, "find_main_worktree_root", lambda _r: primary_root)
    monkeypatch.setattr(remote_reconcile, "worktree_lookup", fake_worktree_lookup)

    def fake_branch_sha(_root: Path, ref: str) -> str:
        if ref == "release/tacos":
            return "local-sha"
        if ref == "tacos/release/tacos":
            return "remote-sha"
        return "some-sha"

    monkeypatch.setattr(remote_reconcile, "branch_sha", fake_branch_sha)
    monkeypatch.setattr(remote_reconcile, "get_head_sha", lambda _root: "completed-feature")
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(remote_reconcile, "read_record", lambda *_a: None)
    monkeypatch.setattr(remote_reconcile, "clear_record", lambda *_a: None)

    git_cmds: list[tuple[str, ...]] = []

    def fake_run_git(cmd: tuple[str, ...], cwd: Path | None = None, **_kw: object) -> GitRunResult:
        git_cmds.append(cmd)
        if cmd[:2] == ("worktree", "add"):
            wt_path = Path(cmd[2])
            wt_path.mkdir(parents=True, exist_ok=True)
            return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:2] == ("worktree", "remove"):
            wt_path = Path(cmd[-1])
            shutil.rmtree(wt_path, ignore_errors=True)
            return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")
        return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_reconcile, "run_git", fake_run_git)
    monkeypatch.setattr(remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseSuccess())
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: False)

    outcome = remote_reconcile.reconcile_target_onto_remote(repo_root, "release/tacos", "tacos")

    assert outcome.reconciled is True
    remove_cmds = [c for c in git_cmds if c[:2] == ("worktree", "remove")]
    assert len(remove_cmds) == 2
    assert str(stale_wt) in str(remove_cmds[0])


def test_reconcile_after_rejected_push_converges_unowned_non_main_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "feature-wt"
    repo_root.mkdir()
    (repo_root / "feature_code.py").write_text("# uncommitted dirty work\n", encoding="utf-8")

    primary_root = tmp_path / "main-repo"
    primary_root.mkdir()

    pushed_targets: list[tuple[str, str]] = []

    class _TacosRemoteClient(RemoteClient):
        def refresh_target(
            self,
            repo_root: Path,
            target: str,
            *,
            timeout_seconds: float,
            remote: str | None = None,
        ) -> str:
            del repo_root, target, timeout_seconds, remote
            return REFRESH_DIVERGED

        def push_branch(
            self,
            repo_root: Path,
            branch: str,
            *,
            remote: str,
            timeout_seconds: float,
        ) -> remote_push_module.PushResult:
            del repo_root, timeout_seconds
            pushed_targets.append((remote, branch))
            return remote_push_module.PushResult(
                remote_push_module.PushStatus.PUSHED,
                remote,
                branch,
                f"pushed {branch} to {remote}",
            )

        def observe_remote_sha(
            self,
            repo_root: Path,
            target: str,
            *,
            remote: str,
        ) -> str | None:
            del repo_root, target, remote
            return None

    client = _TacosRemoteClient()

    monkeypatch.setattr(remote_reconcile, "find_main_worktree_root", lambda _r: primary_root)
    monkeypatch.setattr(
        remote_reconcile, "worktree_lookup", lambda _r, _t: ("not_checked_out", None)
    )

    def fake_branch_sha(_root: Path, ref: str) -> str:
        if ref == "release/tacos":
            return "local-sha"
        if ref == "tacos/release/tacos":
            return "remote-sha"
        return "some-sha"

    monkeypatch.setattr(remote_reconcile, "branch_sha", fake_branch_sha)
    monkeypatch.setattr(remote_reconcile, "get_head_sha", lambda _root: "completed-feature")
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(remote_reconcile, "read_record", lambda *_a: None)
    monkeypatch.setattr(remote_reconcile, "clear_record", lambda *_a: None)

    git_cmds: list[tuple[str, ...]] = []

    def fake_run_git(cmd: tuple[str, ...], cwd: Path | None = None, **_kw: object) -> GitRunResult:
        git_cmds.append(cmd)
        if cmd[:2] == ("worktree", "add"):
            wt_path = Path(cmd[2])
            wt_path.mkdir(parents=True, exist_ok=True)
            return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:2] == ("worktree", "remove"):
            wt_path = Path(cmd[-1])
            shutil.rmtree(wt_path, ignore_errors=True)
            return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")
        return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_reconcile, "run_git", fake_run_git)
    monkeypatch.setattr(remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseSuccess())
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: False)

    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_enabled=True,
            auto_integrate_target="release/tacos",
            auto_integrate_remote="tacos",
            auto_integrate_remote_enabled=True,
        )
    )
    initial_record = RebaseState(
        last_action="landed",
        last_target="release/tacos",
        fast_forwarded=True,
    )

    final_record = reconcile_after_rejected_push(
        config,
        repo_root,
        "release/tacos",
        initial_record,
        remote="tacos",
        remote_client=client,
    )

    assert final_record.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED
    assert final_record.last_remote == "tacos"
    assert final_record.last_push_status == "pushed"
    assert pushed_targets == [("tacos", "release/tacos")]

    assert "main" not in str(pushed_targets)
    assert "origin" not in str(pushed_targets)
    for cmd in git_cmds:
        assert "origin" not in cmd
        assert "main" not in cmd

    assert (repo_root / "feature_code.py").read_text(
        encoding="utf-8"
    ) == "# uncommitted dirty work\n"
