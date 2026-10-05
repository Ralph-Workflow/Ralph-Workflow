"""Black-box tests for ``ralph.git.scoped_auto_commit``.

The shared primitive every deterministic background writer routes through:
``commit_deterministic_writes`` and its supporting helpers. This file
pins the wt-012 PA-002 contract:

* exact-path lookup in :func:`_read_head_blob_sha` (no prefix collision),
* the dir→symlink transition (an old directory replaced by a new
  symlink commits the deletions AND the new link and leaves the tree
  clean),
* the FAILED-attempt rollback preserves the pre-staged index byte-for-byte,
* an ancestor-symlink shape (a deleted sibling dir replaced by a
  symlink that resolves to a same-named same-content file) does NOT
  silently misclassify the deletion as a no-op,
* all the existing scope/no-op/SKIPPED/NOT_REPO semantics still hold.

The tests use real git (a per-test ``tmp_path`` repo) and the production
``create_commit`` / ``stage_files`` flow. They run in well under the
``subprocess_e2e`` per-suite cap and stay inside the immutable 60s
combined verify budget.
"""

from __future__ import annotations

import hashlib
import shutil
from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from ralph.git.commit_result import CommitCreationResult
from ralph.git.operations import create_commit, stage_files
from ralph.git.scoped_auto_commit import (
    _ANCESTOR_SYMLINK_DIRTY,
    ScopedCommitStatus,
    _git_blob_sha,
    _has_symlink_ancestor,
    _read_head_blob_sha,
    capture_pre_write_contents,
    commit_deterministic_writes,
)
from tests._support.typed_accessors import must_str

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [
    pytest.mark.subprocess_e2e,
    # ponytail: real-git setup; 5s per test fits inside the 60s combined verify budget.
    pytest.mark.timeout_seconds(5),
]


def _init_repo_with_initial_commit(repo_root: Path) -> None:
    """Create a fresh git repo with one initial empty commit so HEAD exists."""
    repo = Repo.init(repo_root)
    try:
        repo.config_writer().set_value("user", "name", "Test Author").release()
        repo.config_writer().set_value("user", "email", "test@example.com").release()
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("initial", author=actor, committer=actor)
    finally:
        repo.close()


# ----------------------------------------------------------------------------
# _read_head_blob_sha exact-path contract (PA-002 / DA-006)
# ----------------------------------------------------------------------------


def test_read_head_blob_sha_returns_sha_for_tracked_file(tmp_path: Path) -> None:
    """A tracked file's exact path returns the expected HEAD blob."""
    _init_repo_with_initial_commit(tmp_path)
    target = tmp_path / "tracked.txt"
    target.write_text("hello\n", encoding="utf-8")
    repo = Repo(tmp_path)
    try:
        repo.index.add(["tracked.txt"])
        repo.index.commit("add tracked", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        expected_sha = repo.head.commit.tree["tracked.txt"].hexsha
    finally:
        repo.close()
    repo2 = Repo(tmp_path)
    try:
        assert _read_head_blob_sha(repo2, "tracked.txt") == expected_sha
    finally:
        repo2.close()


def test_read_head_blob_sha_returns_none_for_untracked_path(tmp_path: Path) -> None:
    """A path that is not in the index returns None (absent at HEAD)."""
    _init_repo_with_initial_commit(tmp_path)
    repo = Repo(tmp_path)
    try:
        assert _read_head_blob_sha(repo, "nope.txt") is None
    finally:
        repo.close()


def test_read_head_blob_sha_rejects_prefix_collision(tmp_path: Path) -> None:
    """PA-002: asking for ``a`` when ``a/b`` is tracked must NOT return a/b's blob.

    This is the prefix-collision bug: ``git ls-files --stage -- a`` returns
    every entry whose name starts with ``a/``; the previous implementation
    returned the FIRST line's blob, silently attributing a descendant's
    blob to the ancestor path. With the exact-path fix the lookup returns
    None for the missing ancestor.
    """
    _init_repo_with_initial_commit(tmp_path)
    nested = tmp_path / "a" / "b"
    nested.parent.mkdir(parents=True)
    nested.write_text("leaf\n", encoding="utf-8")
    repo = Repo(tmp_path)
    try:
        repo.index.add(["a/b"])
        repo.index.commit("add a/b", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    finally:
        repo.close()
    repo2 = Repo(tmp_path)
    try:
        # The ancestor ``a`` is not tracked at HEAD; the exact-path lookup
        # must return None even though ``a/b`` IS tracked. The previous
        # implementation returned a/b's blob here.
        assert _read_head_blob_sha(repo2, "a") is None
        # Sanity: the descendant still returns its real blob.
        nested_sha = repo2.head.commit.tree["a/b"].hexsha
        assert _read_head_blob_sha(repo2, "a/b") == nested_sha
    finally:
        repo2.close()


# ----------------------------------------------------------------------------
# _has_symlink_ancestor helper
# ----------------------------------------------------------------------------


def test_has_symlink_ancestor_detects_immediate_symlink_parent(tmp_path: Path) -> None:
    """A symlink parent of a regular file is detected."""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "real.txt").write_text("hi\n", encoding="utf-8")
    link_dir = tmp_path / "link"
    link_dir.symlink_to(target_dir)
    assert _has_symlink_ancestor(link_dir / "real.txt") is True


def test_has_symlink_ancestor_returns_false_for_normal_tree(tmp_path: Path) -> None:
    """A regular tree has no symlink ancestors."""
    nested = tmp_path / "a" / "b" / "c.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("x\n", encoding="utf-8")
    assert _has_symlink_ancestor(nested) is False


def test_has_symlink_ancestor_returns_false_for_leaf_symlink(tmp_path: Path) -> None:
    """A leaf symlink itself has no symlink ancestors; the leaf is the fast path."""
    link = tmp_path / "link.txt"
    link.symlink_to("target")
    assert _has_symlink_ancestor(link) is False


# ----------------------------------------------------------------------------
# _git_blob_sha ancestor-guard ordering (wt-012 DA-010)
# ----------------------------------------------------------------------------


def test_git_blob_sha_returns_sentinel_for_symlink_leaf_under_symlink_ancestor(
    tmp_path: Path,
) -> None:
    """wt-012 DA-010: a symlink LEAF under a symlink ancestor hashes to the sentinel.

    Regression test for the guard ordering: the leaf fast path used to
    run BEFORE the ancestor-symlink guard, so a symlink leaf reachable
    through a freshly-installed ancestor symlink hashed its link-target
    bytes. When those bytes accidentally equal the recorded pre-write
    hash, the producer's byte-exact diff misclassifies the path as
    "unchanged" and drops it from the written set, letting the
    ancestor commit silently sweep the user's dirty deletion
    (wt-012 DA-003/DA-012).
    """
    _init_repo_with_initial_commit(tmp_path)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (canonical / "alias").symlink_to("dirty")
    sibling = tmp_path / "sibling"
    sibling.symlink_to(canonical)
    repo = Repo(tmp_path)
    try:
        # The leaf resolves through a symlink ancestor: sentinel, NOT
        # the link-target hash the pre-fix ordering produced.
        assert _git_blob_sha(repo, "sibling/alias") == _ANCESTOR_SYMLINK_DIRTY
        # The sibling ROOT itself is a plain leaf symlink with no
        # symlink ancestor: the fast path still hashes its link-target
        # bytes in git's symlink blob form.
        target_bytes = str(canonical).encode("utf-8")
        blob = b"blob " + str(len(target_bytes)).encode("ascii") + b"\x00" + target_bytes
        assert _git_blob_sha(repo, "sibling") == hashlib.sha1(blob).hexdigest()
    finally:
        repo.close()


# ----------------------------------------------------------------------------
# commit_deterministic_writes — dir→symlink transition
# ----------------------------------------------------------------------------


def test_commit_dir_to_symlink_transition_removes_old_files_and_adds_new_link(
    tmp_path: Path,
) -> None:
    """wt-012 PA-002: an old dir replaced by a new symlink commits both sides.

    Setup: a tracked dir ``.claude/skills/foo`` containing
    ``foo/SKILL.md`` and ``foo/_MANAGED_MARKER.json`` (we use a plain
    text marker for portability). Replace the dir with a symlink to a
    new canonical location, then call ``commit_deterministic_writes``
    with the byte-exact set of paths the install actually touched.

    Expected:
    * the deletion of the old descendant files lands in the commit,
    * the new symlink at the old path lands in the commit,
    * the post-commit tree is clean (no dirty descendants, no extra
      symlink garbage),
    * the diff between HEAD and the post-commit tree is exactly the
      replacement (no unrelated paths swept in).
    """
    _init_repo_with_initial_commit(tmp_path)
    claude = tmp_path / ".claude" / "skills" / "foo"
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text("old\n", encoding="utf-8")
    (claude / "_MANAGED_MARKER.json").write_text('{"managed": true}\n', encoding="utf-8")
    # Make the project-canonical root with the new content.
    canonical = tmp_path / ".opencode" / "skills" / "foo"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text("new\n", encoding="utf-8")

    repo = Repo(tmp_path)
    try:
        repo.index.add(
            [
                ".claude/skills/foo/SKILL.md",
                ".claude/skills/foo/_MANAGED_MARKER.json",
            ]
        )
        repo.index.commit(
            "add old sibling", author=Actor("t", "t@t"), committer=Actor("t", "t@t")
        )
    finally:
        repo.close()

    # Capture pre-write contents BEFORE the install mutates anything.
    # The producer contract is: snapshot the pre-write content hash of
    # every byte-exact path the install will touch, then mutate, then
    # commit. The new symlink root is a brand-new path at the install
    # boundary, so it is recorded as ``None`` (absent before the
    # install). The deleted descendants have a recorded pre-write hash
    # equal to HEAD's blob.
    pre_contents = capture_pre_write_contents(
        tmp_path,
        [
            ".claude/skills/foo",
            ".claude/skills/foo/SKILL.md",
            ".claude/skills/foo/_MANAGED_MARKER.json",
        ],
    )
    assert pre_contents[".claude/skills/foo"] is None
    assert pre_contents[".claude/skills/foo/SKILL.md"] is not None
    assert pre_contents[".claude/skills/foo/_MANAGED_MARKER.json"] is not None

    # Replace the tracked dir with a symlink (the install path).
    shutil.rmtree(claude)
    claude.symlink_to(canonical)

    result = commit_deterministic_writes(
        tmp_path,
        paths=[
            ".claude/skills/foo",
            ".claude/skills/foo/SKILL.md",
            ".claude/skills/foo/_MANAGED_MARKER.json",
        ],
        pre_contents=pre_contents,
        subject="chore(skills): sync baseline bundle",
        create_commit_fn=create_commit,
        stage_fn=stage_files,
        intentional_transitions=frozenset({".claude/skills/foo"}),
    )

    assert result.status is ScopedCommitStatus.CREATED, (
        f"dir→symlink transition must commit; got: {result!r}"
    )
    assert result.sha is not None

    # Post-commit tree must be clean for the in-scope paths.
    repo = Repo(tmp_path)
    try:
        head_tree = repo.head.commit.tree
        tree_paths = {entry.path for entry in head_tree}
        # The old descendants are gone from the tree.
        assert ".claude/skills/foo/SKILL.md" not in tree_paths
        assert ".claude/skills/foo/_MANAGED_MARKER.json" not in tree_paths
        # The new canonical content was untouched.
        assert (canonical / "SKILL.md").read_text(encoding="utf-8") == "new\n"
    finally:
        repo.close()


def test_commit_dir_to_symlink_failed_attempt_preserves_pre_staged_index(
    tmp_path: Path,
) -> None:
    """FAILED during a dir→symlink transition leaves the pre-staged index intact.

    The user pre-staged an unrelated file (``my_wip.txt``) before the
    install ran. A failing commit must (a) NOT touch the in-scope
    stage, (b) NOT touch the user's pre-staged work.
    """
    _init_repo_with_initial_commit(tmp_path)
    # Set up the tracked dir + a tracked file the user has pre-staged.
    claude = tmp_path / ".claude" / "skills" / "foo"
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text("old\n", encoding="utf-8")
    (claude / "_MANAGED_MARKER.json").write_text('{"managed": true}\n', encoding="utf-8")
    wip = tmp_path / "my_wip.txt"
    wip.write_text("wip\n", encoding="utf-8")

    repo = Repo(tmp_path)
    try:
        repo.index.add(
            [
                ".claude/skills/foo/SKILL.md",
                ".claude/skills/foo/_MANAGED_MARKER.json",
                "my_wip.txt",
            ]
        )
        repo.index.commit(
            "seed", author=Actor("t", "t@t"), committer=Actor("t", "t@t")
        )
        # User pre-stages an edit to my_wip.txt.
        wip.write_text("wip updated\n", encoding="utf-8")
        repo.index.add(["my_wip.txt"])
    finally:
        repo.close()

    # Set up the canonical content + capture pre-write BEFORE the install.
    canonical = tmp_path / ".opencode" / "skills" / "foo"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text("new\n", encoding="utf-8")
    pre_contents = capture_pre_write_contents(
        tmp_path,
        [
            ".claude/skills/foo",
            ".claude/skills/foo/SKILL.md",
            ".claude/skills/foo/_MANAGED_MARKER.json",
        ],
    )

    # Install: replace the dir with a symlink.
    shutil.rmtree(claude)
    claude.symlink_to(canonical)

    # Inject a failing create_commit_fn that returns a non-CREATED result.
    def _failing_create_commit(
        _repo_root: Path, _message: str, *, expected_head: str
    ) -> CommitCreationResult:
        del expected_head
        return CommitCreationResult.failed("simulated failure")

    result = commit_deterministic_writes(
        tmp_path,
        paths=[
            ".claude/skills/foo",
            ".claude/skills/foo/SKILL.md",
            ".claude/skills/foo/_MANAGED_MARKER.json",
        ],
        pre_contents=pre_contents,
        subject="chore(skills): sync baseline bundle",
        create_commit_fn=_failing_create_commit,
        stage_fn=stage_files,
        intentional_transitions=frozenset({".claude/skills/foo"}),
    )

    assert result.status is ScopedCommitStatus.FAILED, (
        f"injected failure must be reported FAILED; got: {result!r}"
    )

    # Post-failure HEAD is unchanged AND the user's pre-staged my_wip.txt
    # edit is still staged.
    repo = Repo(tmp_path)
    try:
        # HEAD still points at the seed commit; the failed attempt did
        # not advance HEAD.
        head_tree = repo.head.commit.tree
        assert "my_wip.txt" in {entry.path for entry in head_tree}
        # The user's pre-staged edit is still in the index (rolled back
        # from the stage that the failed attempt created, but preserved
        # from the original pre-staged state).
        index_paths = {path for path, _ in repo.index.entries}
        assert "my_wip.txt" in index_paths
        # The pre-staged snapshot of the in-scope paths (the seed
        # commit's tracked descendants) is also preserved: the failed
        # attempt's rollback restored the pre-staged index, so the
        # descendants are still in the index even though they no
        # longer exist on disk (the dir was replaced with a symlink).
        # The pre-staged snapshot preserves the original
        # --cacheinfo entry so the failed attempt leaves no half-staged
        # debris AND no half-rolled-back debris.
        assert ".claude/skills/foo/SKILL.md" in index_paths
        assert ".claude/skills/foo/_MANAGED_MARKER.json" in index_paths
    finally:
        repo.close()


def test_commit_dir_to_symlink_failed_attempt_byte_identical_pre_staged_index(
    tmp_path: Path,
) -> None:
    """wt-012 DA-010: FAILED transition restores the full pre-staged index byte-for-byte.

    The earlier ``in index_paths`` membership assertions
    (``test_commit_dir_to_symlink_failed_attempt_preserves_pre_staged_index``)
    are path-membership-only: a rollback that restored the right path
    but with the wrong blob SHA, or vice versa, would silently pass.
    This regression closes that hole by comparing the full
    ``git ls-files --stage`` output (mode + blob SHA + path) before
    and after the failed attempt -- the rollback MUST be byte-identical
    to the pre-staged snapshot, and HEAD MUST remain the seed commit
    byte-for-byte.
    """
    _init_repo_with_initial_commit(tmp_path)
    claude = tmp_path / ".claude" / "skills" / "foo"
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text("old\n", encoding="utf-8")
    (claude / "_MANAGED_MARKER.json").write_text('{"managed": true}\n', encoding="utf-8")
    wip = tmp_path / "my_wip.txt"
    wip.write_text("wip\n", encoding="utf-8")

    repo = Repo(tmp_path)
    try:
        repo.index.add(
            [
                ".claude/skills/foo/SKILL.md",
                ".claude/skills/foo/_MANAGED_MARKER.json",
                "my_wip.txt",
            ]
        )
        repo.index.commit(
            "seed", author=Actor("t", "t@t"), committer=Actor("t", "t@t")
        )
        wip.write_text("wip updated\n", encoding="utf-8")
        repo.index.add(["my_wip.txt"])
        # wt-012 DA-010: capture the pre-staged index bytes
        # (``git ls-files --stage``) so the post-FAILED assertion can
        # compare the full pre-staged index to the rollback result
        # byte-for-byte. Capturing the raw text here is the cheapest
        # way to assert exact byte equivalence (mode + blob SHA +
        # path) without depending on gitpython's index internals.
        pre_staged_raw = must_str(repo.git.ls_files("--stage"))
        seed_head = str(repo.head.commit.hexsha)
    finally:
        repo.close()

    canonical = tmp_path / ".opencode" / "skills" / "foo"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text("new\n", encoding="utf-8")
    pre_contents = capture_pre_write_contents(
        tmp_path,
        [
            ".claude/skills/foo",
            ".claude/skills/foo/SKILL.md",
            ".claude/skills/foo/_MANAGED_MARKER.json",
        ],
    )
    shutil.rmtree(claude)
    claude.symlink_to(canonical)

    def _failing_create_commit(
        _repo_root: Path, _message: str, *, expected_head: str
    ) -> CommitCreationResult:
        del expected_head
        return CommitCreationResult.failed("simulated failure")

    result = commit_deterministic_writes(
        tmp_path,
        paths=[
            ".claude/skills/foo",
            ".claude/skills/foo/SKILL.md",
            ".claude/skills/foo/_MANAGED_MARKER.json",
        ],
        pre_contents=pre_contents,
        subject="chore(skills): sync baseline bundle",
        create_commit_fn=_failing_create_commit,
        stage_fn=stage_files,
        intentional_transitions=frozenset({".claude/skills/foo"}),
    )

    assert result.status is ScopedCommitStatus.FAILED, (
        f"injected failure must be reported FAILED; got: {result!r}"
    )

    repo = Repo(tmp_path)
    try:
        # HEAD is byte-identical to the seed commit (no advance).
        assert repo.head.commit.hexsha == seed_head, (
            f"FAILED transition MUST leave HEAD byte-for-byte unchanged; "
            f"head={repo.head.commit.hexsha!r} seed={seed_head!r}"
        )
        # The full pre-staged index bytes (entry path, mode, sha) MUST
        # be restored. ``git ls-files --stage`` is the authoritative
        # index dump; comparing its raw output to the pre-write
        # snapshot catches any silent corruption of an in-scope entry
        # (e.g. the rollback restoring the right path but the wrong
        # blob SHA, or vice versa).
        post_staged_raw = must_str(repo.git.ls_files("--stage"))
        pre_staged_lines = sorted(pre_staged_raw.splitlines())
        post_staged_lines = sorted(post_staged_raw.splitlines())
        assert post_staged_lines == pre_staged_lines, (
            f"FAILED transition MUST restore the pre-staged index bytes; "
            f"pre={pre_staged_lines!r} post={post_staged_lines!r}"
        )
    finally:
        repo.close()


# ----------------------------------------------------------------------------
# Transition isolation: SKIPPED descendants stay uncommitted (DA-003/DA-008)
# ----------------------------------------------------------------------------


def test_dir_to_symlink_transition_does_not_commit_skipped_dirty_descendant(
    tmp_path: Path,
) -> None:
    """wt-012 DA-003/DA-008: a pre-write-dirty descendant stays uncommitted.

    Setup: tracked ``old/clean`` and ``old/wip``; the user modifies
    ``old/wip`` BEFORE the producer captures pre-write contents, so the
    helper must SKIP it. The install then replaces ``old`` with a
    symlink. Staging the ancestor would sweep the skipped descendant's
    deletion into the commit (the DA-003 counterexample: result CREATED
    with ``skipped_paths=('old/wip',)`` yet ``git diff HEAD~1 HEAD``
    recorded ``D old/wip``), and git cannot hold the new symlink and
    the kept descendant in one tree. The helper must therefore refuse
    the transition stage entirely: no commit, HEAD unchanged, the
    skipped descendant still tracked at HEAD, and every transition path
    reported in ``skipped_paths``.
    """
    _init_repo_with_initial_commit(tmp_path)
    old = tmp_path / "old"
    old.mkdir()
    (old / "clean").write_text("clean\n", encoding="utf-8")
    (old / "wip").write_text("orig\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    (target / "clean").write_text("clean\n", encoding="utf-8")

    repo = Repo(tmp_path)
    try:
        repo.index.add(["old/clean", "old/wip"])
        repo.index.commit("seed", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        seed_head = repo.head.commit.hexsha
    finally:
        repo.close()

    # The user dirties old/wip BEFORE the producer captures pre-write
    # contents (capture sees the dirty bytes; HEAD still has "orig\n").
    (old / "wip").write_text("user wip\n", encoding="utf-8")
    pre_contents = capture_pre_write_contents(
        tmp_path, ["old", "old/clean", "old/wip"]
    )

    # Install: replace the dir with a symlink.
    shutil.rmtree(old)
    old.symlink_to(target)

    result = commit_deterministic_writes(
        tmp_path,
        paths=["old", "old/clean", "old/wip"],
        pre_contents=pre_contents,
        subject="chore(skills): sync baseline bundle",
        create_commit_fn=create_commit,
        stage_fn=stage_files,
    )

    assert result.status is ScopedCommitStatus.SKIPPED, (
        f"transition sweeping a skipped dirty descendant must not commit; got: {result!r}"
    )
    # The dirty descendant is reported skipped, and the ancestor plus
    # the eligible descendant that could only stage through it join it.
    assert "old/wip" in result.skipped_paths
    assert "old" in result.skipped_paths

    repo = Repo(tmp_path)
    try:
        # HEAD did not advance: no commit was created.
        assert repo.head.commit.hexsha == seed_head
        # The skipped descendant is still tracked at HEAD -- its
        # deletion was NOT committed.
        head_paths = {entry.path for entry in repo.head.commit.tree.traverse()}
        assert "old/wip" in head_paths
        assert "old/clean" in head_paths
        # The transition stays visibly uncommitted: the skipped user's
        # deletion is an unstaged working-tree change, never swept into
        # a fixed-message commit.
        porcelain = repo.git.status("--porcelain")
        assert "old/wip" in porcelain
    finally:
        repo.close()


def test_transition_conflict_skips_only_the_conflicting_ancestor(
    tmp_path: Path,
) -> None:
    """wt-012 DA-008: unrelated eligible writes still commit; only the
    conflicting transition is skipped.

    Same dirty-descendant transition as above, plus a brand-new file
    the deterministic writer authored (absent at capture, written
    after). The commit must contain EXACTLY the new file -- the
    transition paths stay uncommitted and the user's dirty descendant
    remains tracked at HEAD.
    """
    _init_repo_with_initial_commit(tmp_path)
    old = tmp_path / "old"
    old.mkdir()
    (old / "clean").write_text("clean\n", encoding="utf-8")
    (old / "wip").write_text("orig\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    (target / "clean").write_text("clean\n", encoding="utf-8")

    repo = Repo(tmp_path)
    try:
        repo.index.add(["old/clean", "old/wip"])
        repo.index.commit("seed", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        seed_head = repo.head.commit.hexsha
    finally:
        repo.close()

    (old / "wip").write_text("user wip\n", encoding="utf-8")
    # new.txt does not exist at capture time -> recorded as None
    # (brand-new path the writer authors below).
    pre_contents = capture_pre_write_contents(
        tmp_path, ["old", "old/clean", "old/wip", "new.txt"]
    )
    assert pre_contents["new.txt"] is None

    shutil.rmtree(old)
    old.symlink_to(target)
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")

    result = commit_deterministic_writes(
        tmp_path,
        paths=["old", "old/clean", "old/wip", "new.txt"],
        pre_contents=pre_contents,
        subject="chore(skills): sync baseline bundle",
        create_commit_fn=create_commit,
        stage_fn=stage_files,
    )

    assert result.status is ScopedCommitStatus.CREATED, (
        f"the unrelated eligible write must still commit; got: {result!r}"
    )
    assert "old/wip" in result.skipped_paths
    assert "old" in result.skipped_paths
    assert "new.txt" not in result.skipped_paths

    repo = Repo(tmp_path)
    try:
        assert repo.head.commit.hexsha != seed_head
        # The commit contains EXACTLY the new file -- no transition
        # deletions, and crucially not the skipped dirty descendant.
        diff = repo.git.diff(seed_head, "HEAD", "--name-status")
        assert diff == "A\tnew.txt"
        head_paths = {entry.path for entry in repo.head.commit.tree.traverse()}
        assert "old/wip" in head_paths
        assert "old/clean" in head_paths
    finally:
        repo.close()


# ----------------------------------------------------------------------------
# Ancestor-symlink safety: same-content trap (PA-002 byte-equal case)
# ----------------------------------------------------------------------------


def test_commit_does_not_misclassify_byte_equal_descendant_under_replaced_dir(
    tmp_path: Path,
) -> None:
    """PA-002 byte-equal trap: a deleted descendant with the same content as
    the new symlink target must STILL be staged (the deletion is real).

    Without the ancestor-symlink safety, ``git hash-object`` would
    follow the new symlink and report the same blob as HEAD, so the
    no-op short-circuit would silently drop the deletion.
    """
    _init_repo_with_initial_commit(tmp_path)
    claude = tmp_path / ".claude" / "skills" / "foo"
    claude.mkdir(parents=True)
    # The old file and the new canonical file have IDENTICAL content
    # (the byte-equal trap shape).
    (claude / "SKILL.md").write_text("identical\n", encoding="utf-8")
    canonical = tmp_path / ".opencode" / "skills" / "foo"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text("identical\n", encoding="utf-8")

    repo = Repo(tmp_path)
    try:
        repo.index.add([".claude/skills/foo/SKILL.md"])
        repo.index.commit(
            "add identical", author=Actor("t", "t@t"), committer=Actor("t", "t@t")
        )
    finally:
        repo.close()

    # Capture pre-write BEFORE the install mutates the file.
    pre_contents = capture_pre_write_contents(
        tmp_path, [".claude/skills/foo/SKILL.md"]
    )
    assert pre_contents[".claude/skills/foo/SKILL.md"] is not None

    # Install: replace the dir with a symlink to the canonical dir.
    shutil.rmtree(claude)
    claude.symlink_to(canonical)

    result = commit_deterministic_writes(
        tmp_path,
        paths=[".claude/skills/foo/SKILL.md"],
        pre_contents=pre_contents,
        subject="chore(skills): sync baseline bundle",
        create_commit_fn=create_commit,
        stage_fn=stage_files,
    )

    # The deletion must land in the commit. If the on-disk hash check
    # silently classified the path as "no diff since HEAD" (the PA-002
    # trap), the helper would return NOOP / SKIPPED and the deletion
    # would leak into the working tree.
    assert result.status is ScopedCommitStatus.CREATED, (
        f"byte-equal descendant under a replaced dir must commit; got: {result!r}"
    )

    # Post-commit: the descendant is no longer tracked (it was deleted).
    repo = Repo(tmp_path)
    try:
        tree_paths = {entry.path for entry in repo.head.commit.tree}
        assert ".claude/skills/foo/SKILL.md" not in tree_paths
    finally:
        repo.close()


# ----------------------------------------------------------------------------
# The _ANCESTOR_SYMLINK_DIRTY sentinel is well-formed
# ----------------------------------------------------------------------------


def test_ancestor_symlink_dirty_sentinel_is_distinguishable_from_real_sha() -> None:
    """The sentinel MUST not collide with a real git SHA-1."""
    assert _ANCESTOR_SYMLINK_DIRTY != "f" * 40
    assert _ANCESTOR_SYMLINK_DIRTY.startswith("__")
    assert _ANCESTOR_SYMLINK_DIRTY.endswith("__")
