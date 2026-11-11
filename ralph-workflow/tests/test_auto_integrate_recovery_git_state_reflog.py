from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from ralph.pipeline._auto_integrate_recovery_git_state import (
    detached_branch_name,
    reattach_head_to_branch,
)

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(10),
]


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(root: Path, name: str = "Test") -> None:
    from git import Repo

    repo = Repo.init(root)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", name)
        cfg.set_value("user", "email", f"{name.lower()}@test.com")
    (root / "file.txt").write_text("initial\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    repo.close()


def _detached_at(root: Path, target: str) -> None:
    rc = _run_git(root, "checkout", "--detach", target).returncode
    assert rc == 0, _run_git(root, "checkout", "--detach", target)


def _append_synthetic_reflog(root: Path, sha: str, subject: str) -> None:
    logs_head = root / ".git" / "logs" / "HEAD"
    original = logs_head.read_text() if logs_head.exists() else ""
    future_ts = int(time.time()) + 100_000
    synthetic = f"{sha} {sha} T <t@t.com> {future_ts} +0000\t{subject}\n"
    logs_head.write_text(original + synthetic)


def _commit_file(root: Path, filename: str, content: str, message: str) -> str:
    from git import Repo

    (root / filename).write_text(content)
    repo = Repo(root)
    try:
        repo.index.add([filename])
        commit = repo.index.commit(message)
        return commit.hexsha
    finally:
        repo.close()


def _head_oid(root: Path) -> str:
    from git import Repo

    repo = Repo(root)
    sha = repo.head.commit.hexsha
    repo.close()
    return sha


class TestDetachedBranchNameRealGit:
    def test_returns_none_when_detached_at_sha(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)

        _run_git(tmp_path, "checkout", "-b", "feature")
        sha = _commit_file(tmp_path, "feature.txt", "feat\n", "add feature")

        _detached_at(tmp_path, sha)

        result = detached_branch_name(tmp_path)
        assert result is None

    def test_returns_none_when_candidate_is_not_local_branch(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)

        _run_git(tmp_path, "checkout", "-b", "main")
        _run_git(tmp_path, "checkout", "-b", "feature")

        sha_result = _run_git(tmp_path, "rev-parse", "HEAD")
        sha = sha_result.stdout.strip()

        _detached_at(tmp_path, sha)

        result = detached_branch_name(tmp_path)
        assert result is None

    def test_returns_none_on_empty_reflog(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)

        result = detached_branch_name(tmp_path)
        assert result is None

    def test_returns_none_on_malformed_reflog(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        _run_git(tmp_path, "checkout", "-b", "feature")

        git_dir = tmp_path / ".git"
        logs_head = git_dir / "logs" / "HEAD"
        original = logs_head.read_text()
        logs_head.write_text(
            "0000000000000000000000000000000000000000 "
            "0000000000000000000000000000000000000000 .mailmap: garbage line\n"
        )

        try:
            result = detached_branch_name(tmp_path)
            assert result is None
        finally:
            logs_head.write_text(original)

    def test_returns_none_when_newest_candidate_branch_is_strictly_behind(
        self, tmp_path: Path
    ) -> None:
        _init_repo(tmp_path)

        _run_git(tmp_path, "checkout", "-b", "main")
        main_sha = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        _run_git(tmp_path, "checkout", "-b", "feature")
        new_sha = _commit_file(tmp_path, "feat.txt", "feat\n", "feature commit")
        _detached_at(tmp_path, new_sha)

        head_now = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        assert head_now == new_sha
        assert head_now != main_sha

        result = detached_branch_name(tmp_path)
        assert result is None

    def test_returns_none_when_rev_parse_head_fails(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        _run_git(tmp_path, "checkout", "-b", "feature")
        feature_sha = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        _detached_at(tmp_path, feature_sha)
        _commit_file(tmp_path, "x.txt", "x\n", "detached work")

        git_dir = tmp_path / ".git"
        head_path = git_dir / "HEAD"
        original = head_path.read_text()
        head_path.write_text("not a valid ref")
        try:
            result = detached_branch_name(tmp_path)
            assert result is None
        finally:
            head_path.write_text(original)

    def test_recovery_after_commit_on_detached_head(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)

        _run_git(tmp_path, "checkout", "-b", "develop")
        develop_sha = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()

        _detached_at(tmp_path, develop_sha)

        _commit_file(tmp_path, "c1.txt", "c1\n", "C1")
        _commit_file(tmp_path, "c2.txt", "c2\n", "C2")

        result = detached_branch_name(tmp_path)
        assert result is None

    def test_recovers_when_branch_tip_equals_detached_head(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        _run_git(tmp_path, "checkout", "-b", "main")
        _run_git(tmp_path, "branch", "feature")
        sha = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        _detached_at(tmp_path, sha)

        _append_synthetic_reflog(tmp_path, sha, "checkout: moving from main to feature")

        result = detached_branch_name(tmp_path)
        assert result == "feature"

    def test_returns_none_when_newest_candidate_branch_tip_is_behind_detached_head(
        self, tmp_path: Path
    ) -> None:
        _init_repo(tmp_path)
        _run_git(tmp_path, "checkout", "-b", "main")
        sha_main = _commit_file(tmp_path, "m.txt", "m\n", "main c2")
        _detached_at(tmp_path, sha_main)
        _commit_file(tmp_path, "d.txt", "d\n", "detached c3")
        detached_head_oid = _head_oid(tmp_path)
        assert detached_head_oid != sha_main
        _append_synthetic_reflog(tmp_path, detached_head_oid, "checkout: moving from main to main")

        result = detached_branch_name(tmp_path)
        assert result is None

    def test_does_not_fall_through_to_older_reflog_entry(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        _run_git(tmp_path, "checkout", "-b", "main")
        sha_c1 = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        sha_c2 = _commit_file(tmp_path, "m.txt", "m\n", "main c2")
        _run_git(tmp_path, "checkout", sha_c1)
        _run_git(tmp_path, "checkout", "-b", "feature")
        sha_c3 = _commit_file(tmp_path, "f.txt", "f\n", "feature c3")
        _run_git(tmp_path, "branch", "oldbranch", sha_c1)
        _detached_at(tmp_path, sha_c1)
        assert _head_oid(tmp_path) == sha_c1
        assert _run_git(tmp_path, "rev-parse", "refs/heads/main").stdout.strip() == sha_c2
        assert _run_git(tmp_path, "rev-parse", "refs/heads/feature").stdout.strip() == sha_c3
        assert _run_git(tmp_path, "rev-parse", "refs/heads/oldbranch").stdout.strip() == sha_c1

        base_ts = int(time.time()) + 100_000
        logs_head = tmp_path / ".git" / "logs" / "HEAD"
        e_old = f"{sha_c1} {sha_c1} T <t@t.com> {base_ts - 1} +0000\tcheckout: moving from main to oldbranch\n"
        e_new = f"{sha_c1} {sha_c1} T <t@t.com> {base_ts} +0000\tcheckout: moving from main to feature\n"
        logs_head.write_text(e_old + e_new)

        result = detached_branch_name(tmp_path)
        assert result is None

    def test_reattach_preserves_head_oid_and_work(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        _run_git(tmp_path, "checkout", "-b", "main")
        _run_git(tmp_path, "branch", "feature")
        sha = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        _detached_at(tmp_path, sha)
        detached_head_oid = _commit_file(tmp_path, "work.txt", "work\n", "work")
        work_before = (tmp_path / "work.txt").read_text()

        _run_git(tmp_path, "branch", "-f", "feature", detached_head_oid)

        _append_synthetic_reflog(
            tmp_path, detached_head_oid, "checkout: moving from main to feature"
        )
        result = detached_branch_name(tmp_path)
        assert result == "feature"

        reattach_head_to_branch(tmp_path, "feature")
        head_after = _head_oid(tmp_path)
        assert head_after == detached_head_oid
        assert (tmp_path / "work.txt").read_text() == work_before
        symbolic = _run_git(tmp_path, "symbolic-ref", "HEAD").stdout.strip()
        assert symbolic == "refs/heads/feature"
