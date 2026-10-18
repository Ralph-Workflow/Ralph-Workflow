"""Unit tests for the reflog-based detached_branch_name recovery seam.

These tests use real Git in temporary directories to verify that:
1. The function correctly recovers the original branch from a detached HEAD
   state where the most recent checkout was to a branch name and the branch
   tip equals detached HEAD (the exact-tip invariant).
2. It returns None when the most recent checkout was to a SHA (explicit detached).
3. It returns None when a candidate branch tip is strictly behind detached
   HEAD (an older branch must not be returned as the recovery observation).
4. It returns None on an empty reflog (brand new repo).
5. It returns None on malformed/garbage reflog content (fail-closed).
6. The public-boundary reattach preserves HEAD OID and the working tree.
"""

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
    """Create a minimal git repo with a first commit."""
    _run_git(root, "init")
    _run_git(root, "config", "user.email", f"{name.lower()}@test.com")
    _run_git(root, "config", "user.name", name)
    (root / "file.txt").write_text("initial\n")
    _run_git(root, "add", ".")
    _run_git(root, "commit", "-m", "initial")


def _detached_at(root: Path, target: str) -> None:
    """Detach HEAD at ``target`` (a SHA or ref)."""
    rc = _run_git(root, "checkout", "--detach", target).returncode
    assert rc == 0, _run_git(root, "checkout", "--detach", target)


def _append_synthetic_reflog(root: Path, sha: str, subject: str) -> None:
    """Append a synthetic reflog entry to .git/logs/HEAD (most-recent in display).

    Git appends new reflog entries at the bottom of the file; ``git reflog``
    shows them in reverse, so the bottom of the file is the most-recent
    displayed entry.  The synthetic entry uses a future timestamp and the
    realistic ``Name <email> <ts> <tz>`` author format so git's reflog
    parser accepts it.
    """
    logs_head = root / ".git" / "logs" / "HEAD"
    original = logs_head.read_text() if logs_head.exists() else ""
    future_ts = int(time.time()) + 100_000
    synthetic = f"{sha} {sha} T <t@t.com> {future_ts} +0000\t{subject}\n"
    logs_head.write_text(original + synthetic)


def _head_oid(root: Path) -> str:
    return _run_git(root, "rev-parse", "HEAD").stdout.strip()


class TestDetachedBranchNameRealGit:
    """Test detached_branch_name against real Git in tmp_path."""

    def test_returns_none_when_detached_at_sha(self, tmp_path: Path) -> None:
        """Detaching at a SHA (even if it is a branch tip) is an explicit choice.

        The most recent checkout entry's "to" field is a SHA, not a branch name.
        The user left the branch context deliberately; no branch to recover to.
        """
        _init_repo(tmp_path)

        # Create a named branch
        _run_git(tmp_path, "checkout", "-b", "feature")
        (tmp_path / "feature.txt").write_text("feat\n")
        _run_git(tmp_path, "add", ".")
        _run_git(tmp_path, "commit", "-m", "add feature")

        # Get the SHA of the branch tip
        sha_result = _run_git(tmp_path, "rev-parse", "HEAD")
        sha = sha_result.stdout.strip()

        # Detach HEAD at the branch tip SHA
        _detached_at(tmp_path, sha)

        # Most recent reflog entry: "checkout: moving from feature to <sha>"
        # The "to" part is a SHA, not a branch name.
        # Recovery must return None (explicit detached at SHA).
        result = detached_branch_name(tmp_path)
        assert result is None

    def test_returns_none_when_candidate_is_not_local_branch(self, tmp_path: Path) -> None:
        """When most recent checkout's 'to' part is a SHA (not a branch), return None.

        The user checked out an arbitrary SHA that is not a branch name.
        This is an explicit detached checkout; no branch context to recover.
        """
        _init_repo(tmp_path)

        # Create two branches; main and feature are at different commits
        _run_git(tmp_path, "checkout", "-b", "main")
        _run_git(tmp_path, "checkout", "-b", "feature")

        sha_result = _run_git(tmp_path, "rev-parse", "HEAD")
        sha = sha_result.stdout.strip()

        # Detach HEAD at a SHA that is feature's tip
        _detached_at(tmp_path, sha)

        # Most recent reflog entry has a SHA as the "to" part.
        # Recovery must return None.
        result = detached_branch_name(tmp_path)
        assert result is None

    def test_returns_none_on_empty_reflog(self, tmp_path: Path) -> None:
        """Brand new repo with no checkout reflog entries returns None."""
        _init_repo(tmp_path)

        # A fresh repo after only init+commit has no checkout entries in reflog.
        # The only reflog entry is "commit (initial)" -- no checkout marker.
        result = detached_branch_name(tmp_path)
        assert result is None

    def test_returns_none_on_malformed_reflog(self, tmp_path: Path) -> None:
        """Garbage in the reflog is fail-closed: returns None."""
        _init_repo(tmp_path)
        _run_git(tmp_path, "checkout", "-b", "feature")

        # Replace the reflog with garbage (keep repo valid, but no valid entries)
        git_dir = tmp_path / ".git"
        logs_head = git_dir / "logs" / "HEAD"
        original = logs_head.read_text()
        # Write only garbage -- no valid reflog entries with null-byte separators
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
        """An older branch behind detached HEAD must NOT win the recovery.

        A new reflog entry names a SHA (the most recent checkout was an
        explicit detached at a fresh commit).  The next-most-recent entry
        names ``feature`` whose tip is strictly behind detached HEAD.  The
        recovery must fail-closed and return ``None`` -- the most-recent
        entry is the only authoritative observation, and an older branch
        that is not the detached HEAD is not a valid recovery.
        """
        _init_repo(tmp_path)

        # Build main with one commit, then branch feature and add a second commit.
        _run_git(tmp_path, "checkout", "-b", "main")
        main_sha = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        _run_git(tmp_path, "checkout", "-b", "feature")
        (tmp_path / "feat.txt").write_text("feat\n")
        _run_git(tmp_path, "add", ".")
        _run_git(tmp_path, "commit", "-m", "feature commit")

        # Detach HEAD at the new commit (a SHA) -- this is the newest entry.
        new_sha = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        _detached_at(tmp_path, new_sha)

        # Sanity: HEAD is detached and not at main_sha.
        head_now = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        assert head_now == new_sha
        assert head_now != main_sha

        result = detached_branch_name(tmp_path)
        assert result is None

    def test_returns_none_when_rev_parse_head_fails(self, tmp_path: Path) -> None:
        """When ``git rev-parse HEAD`` itself fails, the helper must fail closed.

        Simulates an unreadable HEAD (corrupt ``HEAD`` file) by replacing it
        with garbage: ``rev-parse --verify HEAD`` returns non-zero.  Even
        though an earlier reflog entry names a valid branch, the unreadable
        HEAD makes the recovery observation unknown, so the helper must
        return ``None`` and never fall through to the older entry.
        """
        _init_repo(tmp_path)
        _run_git(tmp_path, "checkout", "-b", "feature")
        feature_sha = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        # Detach at feature's tip -- this writes a SHA into the reflog's
        # most-recent entry, but a brand new commit on detached HEAD will
        # then make an older reflog entry name ``feature`` as the to-field.
        _detached_at(tmp_path, feature_sha)
        (tmp_path / "x.txt").write_text("x\n")
        _run_git(tmp_path, "add", ".")
        _run_git(tmp_path, "commit", "-m", "detached work")

        # Corrupt HEAD so rev-parse fails.
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
        """Commits made on detached HEAD: recovery returns None.

        User was on 'develop', detached at its SHA, made C1 and C2 on top,
        then stopped. The most recent checkout entry is to the SHA (not a branch),
        so we must NOT recover (explicit detached). This is correct -- making
        commits on a detached HEAD then recovering to the original branch would
        lose those commits unless the branch is rebased/fast-forwarded.
        """
        _init_repo(tmp_path)

        # Set up: develop branch
        _run_git(tmp_path, "checkout", "-b", "develop")
        develop_sha = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()

        # Detach at develop's SHA
        _detached_at(tmp_path, develop_sha)

        # Make commits on the detached HEAD
        (tmp_path / "c1.txt").write_text("c1\n")
        _run_git(tmp_path, "add", ".")
        _run_git(tmp_path, "commit", "-m", "C1")
        (tmp_path / "c2.txt").write_text("c2\n")
        _run_git(tmp_path, "add", ".")
        _run_git(tmp_path, "commit", "-m", "C2")

        # Most recent checkout entry is still "checkout: moving from develop to <sha>"
        # (the SHA of develop's tip). The "to" part is a SHA, not "develop".
        # Recovery returns None (explicit detached -- commits were made).
        result = detached_branch_name(tmp_path)
        assert result is None

    def test_recovers_when_branch_tip_equals_detached_head(self, tmp_path: Path) -> None:
        """The exact-tip invariant: a branch whose tip EQUALS detached HEAD recovers.

        Build ``main`` and ``feature`` at the same OID, then detach HEAD.
        Inject a synthetic reflog entry at the bottom of the file (newest in
        git's display) whose destination is ``feature``.  The branch exists,
        its tip equals detached HEAD, so the recovery must return "feature".
        """
        _init_repo(tmp_path)
        _run_git(tmp_path, "checkout", "-b", "main")
        _run_git(tmp_path, "branch", "feature")
        sha = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        _detached_at(tmp_path, sha)

        # Inject a synthetic reflog entry naming ``feature`` as the
        # destination.  Appended to the bottom of the file (most recent
        # in git's display), so the function will see it first.
        _append_synthetic_reflog(tmp_path, sha, "checkout: moving from main to feature")

        result = detached_branch_name(tmp_path)
        assert result == "feature"

    def test_returns_none_when_newest_candidate_branch_tip_is_behind_detached_head(
        self, tmp_path: Path
    ) -> None:
        """Strict-tip invariant: a branch tip STRICTLY BEHIND detached HEAD is rejected.

        The pre-fix implementation accepted any branch whose tip is an
        ancestor of detached HEAD (via ``is_ancestor``), then called
        ``reattach_head_to_branch`` which only changes the symbolic
        ref.  That silently dropped the detached commits because the
        branch tip is behind detached HEAD (a ``refs/heads/X``-only
        reattach cannot fast-forward).

        Test path:
        1. Build ``main`` at sha_main, advance it to sha_main_2.
        2. Detach HEAD at sha_main_2 (so HEAD == main tip).
        3. Add a new commit on detached HEAD -- now HEAD is forward of
           ``main``'s tip.
        4. Inject a synthetic reflog entry naming ``main`` as the
           destination.  ``main``'s tip (sha_main_2) is strictly
           behind detached HEAD (the new commit).  The pre-fix
           ``is_ancestor`` check would have returned ``True``
           (``sha_main_2`` is an ancestor of the new commit) and the
           helper would have returned ``"main"`` -- the WRONG branch.
           The fix requires exact-tip equality and returns ``None``.
        """
        _init_repo(tmp_path)
        _run_git(tmp_path, "checkout", "-b", "main")
        (tmp_path / "m.txt").write_text("m\n")
        _run_git(tmp_path, "add", ".")
        _run_git(tmp_path, "commit", "-m", "main c2")
        sha_main = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        # Detach HEAD at sha_main (so HEAD == main tip).
        _detached_at(tmp_path, sha_main)
        # Add a new commit on detached HEAD -- now HEAD is forward of
        # ``main``'s tip.
        (tmp_path / "d.txt").write_text("d\n")
        _run_git(tmp_path, "add", ".")
        _run_git(tmp_path, "commit", "-m", "detached c3")
        detached_head_oid = _head_oid(tmp_path)
        assert detached_head_oid != sha_main
        # Inject a synthetic reflog entry naming ``main`` as the
        # newest destination.  ``main``'s tip (sha_main) is behind
        # detached HEAD (the new commit).
        _append_synthetic_reflog(tmp_path, detached_head_oid, "checkout: moving from main to main")

        result = detached_branch_name(tmp_path)
        assert result is None

    def test_does_not_fall_through_to_older_reflog_entry(self, tmp_path: Path) -> None:
        """Fail-closed: a failed newest-entry check must NOT scan older entries.

        The pre-fix implementation's ``continue`` on a failed
        ``is_ancestor`` check allowed an older reflog entry to win.
        This silently reattached to the wrong branch when the newest
        observation named a forward branch but an older entry named a
        backward branch whose tip happened to be an ancestor of
        detached HEAD.

        Concretely: with a real-Git repo, the newest reflog entry
        names ``feature`` (forward of detached HEAD) and an older
        entry names ``oldbranch`` (equal to detached HEAD).  The
        pre-fix code rejected ``feature`` via ``is_ancestor``, then
        fell through to ``oldbranch`` and returned it -- the wrong
        branch.  The fix returns ``None`` (fail-closed).

        This is the regression that broke invariant 10: an UNKNOWN
        forward observation must not authorize a wrong-older
        reattachment.
        """
        _init_repo(tmp_path)
        # Create main at C1.
        _run_git(tmp_path, "checkout", "-b", "main")
        sha_c1 = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        # Advance main to C2.
        (tmp_path / "m.txt").write_text("m\n")
        _run_git(tmp_path, "add", ".")
        _run_git(tmp_path, "commit", "-m", "main c2")
        sha_c2 = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        # Branch feature at C1, advance to C3.
        _run_git(tmp_path, "checkout", sha_c1)
        _run_git(tmp_path, "checkout", "-b", "feature")
        (tmp_path / "f.txt").write_text("f\n")
        _run_git(tmp_path, "add", ".")
        _run_git(tmp_path, "commit", "-m", "feature c3")
        sha_c3 = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        # Create oldbranch at C1 (behind detached HEAD once we detach).
        _run_git(tmp_path, "branch", "oldbranch", sha_c1)
        # Detach HEAD at C1.
        _detached_at(tmp_path, sha_c1)
        assert _head_oid(tmp_path) == sha_c1
        assert _run_git(tmp_path, "rev-parse", "refs/heads/main").stdout.strip() == sha_c2
        assert _run_git(tmp_path, "rev-parse", "refs/heads/feature").stdout.strip() == sha_c3
        assert _run_git(tmp_path, "rev-parse", "refs/heads/oldbranch").stdout.strip() == sha_c1

        # Inject two synthetic reflog entries: ``feature`` first
        # (newest, forward of detached HEAD), then ``oldbranch`` (older,
        # equal to detached HEAD).  The function must return ``None``
        # because no branch tip equals detached HEAD; the pre-fix
        # code would have returned ``oldbranch`` via fall-through.
        base_ts = int(time.time()) + 100_000
        logs_head = tmp_path / ".git" / "logs" / "HEAD"
        # Oldest of the two (smaller timestamp): to oldbranch.
        e_old = f"{sha_c1} {sha_c1} T <t@t.com> {base_ts - 1} +0000\tcheckout: moving from main to oldbranch\n"
        # Newest: to feature.
        e_new = f"{sha_c1} {sha_c1} T <t@t.com> {base_ts} +0000\tcheckout: moving from main to feature\n"
        logs_head.write_text(e_old + e_new)

        result = detached_branch_name(tmp_path)
        assert result is None

    def test_reattach_preserves_head_oid_and_work(self, tmp_path: Path) -> None:
        """Public boundary: reattach preserves HEAD OID and the work tree.

        This is the regression the previous implementation failed to detect:
        ``reattach_head_to_branch`` only changes the symbolic ref, so if a
        branch tip is strictly behind detached HEAD the call would silently
        drop the detached commits.  The recovery helper must therefore only
        return a branch whose tip equals detached HEAD, and the reattach
        must preserve HEAD OID and the working tree byte-for-byte.

        Test path:
        1. Build ``main`` and ``feature`` at the same OID, detach HEAD.
           Move ``feature`` to detached HEAD so the branch tip equals
           detached HEAD.  Add a tracked file in the working tree.
        2. Inject a synthetic reflog entry naming ``feature``.  Verify the
           recovery returns "feature".
        3. Call ``reattach_head_to_branch`` directly and prove HEAD OID and
           the working tree are byte-identical before vs. after, and that
           ``HEAD`` now resolves symbolically to ``refs/heads/feature``.
        """
        _init_repo(tmp_path)
        _run_git(tmp_path, "checkout", "-b", "main")
        _run_git(tmp_path, "branch", "feature")
        sha = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        _detached_at(tmp_path, sha)
        # Working-tree content is preserved across reattach; the
        # checkout above left an untracked-but-touched tree, so record
        # the state before recovery.
        (tmp_path / "work.txt").write_text("work\n")
        _run_git(tmp_path, "add", ".")
        _run_git(tmp_path, "commit", "-m", "work")
        detached_head_oid = _head_oid(tmp_path)
        work_before = (tmp_path / "work.txt").read_text()

        # Move ``feature`` so its tip equals the current detached HEAD.
        # This is the only condition under which symbolic-ref reattachment
        # is safe: the work is already on the branch tip, so HEAD just
        # reattaches without losing the detached commits.
        _run_git(tmp_path, "branch", "-f", "feature", detached_head_oid)

        # Inject a synthetic reflog entry naming ``feature`` as the
        # destination; feature's tip now equals detached HEAD.
        _append_synthetic_reflog(
            tmp_path, detached_head_oid, "checkout: moving from main to feature"
        )
        result = detached_branch_name(tmp_path)
        assert result == "feature"

        # Reattach and verify HEAD OID + working tree are preserved.
        reattach_head_to_branch(tmp_path, "feature")
        head_after = _head_oid(tmp_path)
        assert head_after == detached_head_oid
        assert (tmp_path / "work.txt").read_text() == work_before
        symbolic = _run_git(tmp_path, "symbolic-ref", "HEAD").stdout.strip()
        assert symbolic == "refs/heads/feature"
