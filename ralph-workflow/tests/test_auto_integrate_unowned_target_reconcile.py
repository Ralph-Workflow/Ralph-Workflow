"""Tests for auto-integration remote reconciliation with no target owner (D2/O2).

Verifies that when no worktree owns the configured target branch:
- Reconciles in a temporary worktree without commandeering dirty feature work.
- Interruption or resolver failures clean up all temporary resources.
- Stale temporary worktrees from prior crashes are safely recovered.
- Divergence on configured non-main target (e.g. release/tacos) converges
  without touching main/origin.
"""

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


@pytest.fixture(autouse=True)
def initiating_feature_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote_reconcile, "get_head_sha", lambda _root: "completed-feature")
    monkeypatch.setattr(remote_reconcile, "branch_sha", lambda _root, _ref: "before")


def test_unowned_target_reconciles_via_temporary_workspace_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """D2/O2: target with no owning worktree reconciles in a temporary workspace."""
    repo_root = tmp_path / "feature-wt"
    repo_root.mkdir()
    (repo_root / "feature.txt").write_text("feature work\n", encoding="utf-8")

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
            wt_path = Path(cmd[3]) if len(cmd) > 3 and cmd[2] == "--force" else Path(cmd[2])
            shutil.rmtree(wt_path, ignore_errors=True)
            return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")
        return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_reconcile, "run_git", fake_run_git)

    rebase_calls: list[Path] = []

    def fake_rebase_onto(onto: str, repo_root: Path) -> RebaseSuccess:
        rebase_calls.append(repo_root)
        return RebaseSuccess()

    monkeypatch.setattr(remote_reconcile, "rebase_onto", fake_rebase_onto)
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: False)

    outcome = remote_reconcile.reconcile_target_onto_remote(repo_root, "release/tacos", "tacos")

    assert outcome.reconciled is True
    assert outcome.reason == ""
    assert len(rebase_calls) == 1
    assert rebase_calls[0] != repo_root
    assert rebase_calls[0].name.startswith("ralph-reconcile-release_tacos-")
    add_cmds = [c for c in git_cmds if c[:2] == ("worktree", "add")]
    remove_cmds = [c for c in git_cmds if c[:2] == ("worktree", "remove")]
    assert len(add_cmds) == 1
    assert len(remove_cmds) == 1
    assert (repo_root / "feature.txt").read_text(encoding="utf-8") == "feature work\n"
    assert repo_root in cleared_records


def test_unowned_target_interruption_cleans_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """D2/O2: interruption or resolver failure in temporary workspace cleans all resources."""
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
            wt_path = Path(cmd[3]) if len(cmd) > 3 and cmd[2] == "--force" else Path(cmd[2])
            shutil.rmtree(wt_path, ignore_errors=True)
            return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")
        return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_reconcile, "run_git", fake_run_git)

    monkeypatch.setattr(
        remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseConflicts("conflict")
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
    assert "resolution interrupted" in outcome.reason
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
    """D2/O2: recovers from a stale temporary worktree left by an interrupted crash."""
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
            wt_path = Path(cmd[3]) if len(cmd) > 3 and cmd[2] == "--force" else Path(cmd[2])
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
    """D2/O2/AC5/AC9: release/tacos divergence converges with no target owner, no main/origin access."""
    repo_root = tmp_path / "feature-wt"
    repo_root.mkdir()
    (repo_root / "feature_code.py").write_text("# uncommitted dirty work\n", encoding="utf-8")

    primary_root = tmp_path / "main-repo"
    primary_root.mkdir()

    pushed_targets: list[tuple[str, str]] = []

    class _TacosRemoteClient(RemoteClient):
        def refresh_target(
            self,
            _repo: Path,
            _target: str,
            *,
            timeout_seconds: float,
            remote: str | None = None,
        ) -> str:
            return REFRESH_DIVERGED

        def push_branch(
            self,
            _repo: Path,
            branch: str,
            *,
            remote: str,
            timeout_seconds: float,
        ) -> remote_push_module.PushResult:
            pushed_targets.append((remote, branch))
            return remote_push_module.PushResult(
                remote_push_module.PushStatus.PUSHED,
                remote,
                branch,
                f"pushed {branch} to {remote}",
            )

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
            wt_path = Path(cmd[3]) if len(cmd) > 3 and cmd[2] == "--force" else Path(cmd[2])
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
