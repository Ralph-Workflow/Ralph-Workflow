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
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(5),
]


def _init_repo_with_initial_commit(repo_root: Path) -> None:
    repo = Repo.init(repo_root)
    try:
        repo.config_writer().set_value("user", "name", "Test Author").release()
        repo.config_writer().set_value("user", "email", "test@example.com").release()
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("initial", author=actor, committer=actor)
    finally:
        repo.close()


def test_read_head_blob_sha_returns_sha_for_tracked_file(tmp_path: Path) -> None:
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
    _init_repo_with_initial_commit(tmp_path)
    repo = Repo(tmp_path)
    try:
        assert _read_head_blob_sha(repo, "nope.txt") is None
    finally:
        repo.close()


def test_read_head_blob_sha_rejects_prefix_collision(tmp_path: Path) -> None:
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
        assert _read_head_blob_sha(repo2, "a") is None
        nested_sha = repo2.head.commit.tree["a/b"].hexsha
        assert _read_head_blob_sha(repo2, "a/b") == nested_sha
    finally:
        repo2.close()


def test_has_symlink_ancestor_detects_immediate_symlink_parent(tmp_path: Path) -> None:
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "real.txt").write_text("hi\n", encoding="utf-8")
    link_dir = tmp_path / "link"
    link_dir.symlink_to(target_dir)
    assert _has_symlink_ancestor(link_dir / "real.txt") is True


def test_has_symlink_ancestor_returns_false_for_normal_tree(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("x\n", encoding="utf-8")
    assert _has_symlink_ancestor(nested) is False


def test_has_symlink_ancestor_returns_false_for_leaf_symlink(tmp_path: Path) -> None:
    link = tmp_path / "link.txt"
    link.symlink_to("target")
    assert _has_symlink_ancestor(link) is False


def test_git_blob_sha_returns_sentinel_for_symlink_leaf_under_symlink_ancestor(
    tmp_path: Path,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (canonical / "alias").symlink_to("dirty")
    sibling = tmp_path / "sibling"
    sibling.symlink_to(canonical)
    repo = Repo(tmp_path)
    try:
        assert _git_blob_sha(repo, "sibling/alias") == _ANCESTOR_SYMLINK_DIRTY
        target_bytes = str(canonical).encode("utf-8")
        blob = b"blob " + str(len(target_bytes)).encode("ascii") + b"\x00" + target_bytes
        assert _git_blob_sha(repo, "sibling") == hashlib.sha1(blob).hexdigest()
    finally:
        repo.close()


def test_commit_dir_to_symlink_transition_removes_old_files_and_adds_new_link(
    tmp_path: Path,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    claude = tmp_path / ".claude" / "skills" / "foo"
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text("old\n", encoding="utf-8")
    (claude / "_MANAGED_MARKER.json").write_text('{"managed": true}\n', encoding="utf-8")
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
        repo.index.commit("add old sibling", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    finally:
        repo.close()

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

    repo = Repo(tmp_path)
    try:
        head_tree = repo.head.commit.tree
        tree_paths = {entry.path for entry in head_tree}
        assert ".claude/skills/foo/SKILL.md" not in tree_paths
        assert ".claude/skills/foo/_MANAGED_MARKER.json" not in tree_paths
        assert (canonical / "SKILL.md").read_text(encoding="utf-8") == "new\n"
    finally:
        repo.close()


def test_commit_dir_to_symlink_failed_attempt_preserves_pre_staged_index(
    tmp_path: Path,
) -> None:
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
        repo.index.commit("seed", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        wip.write_text("wip updated\n", encoding="utf-8")
        repo.index.add(["my_wip.txt"])
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
        head_tree = repo.head.commit.tree
        assert "my_wip.txt" in {entry.path for entry in head_tree}
        index_paths = {path for path, _ in repo.index.entries}
        assert "my_wip.txt" in index_paths
        assert ".claude/skills/foo/SKILL.md" in index_paths
        assert ".claude/skills/foo/_MANAGED_MARKER.json" in index_paths
    finally:
        repo.close()


def test_commit_dir_to_symlink_failed_attempt_byte_identical_pre_staged_index(
    tmp_path: Path,
) -> None:
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
        repo.index.commit("seed", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
        wip.write_text("wip updated\n", encoding="utf-8")
        repo.index.add(["my_wip.txt"])
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
        assert repo.head.commit.hexsha == seed_head, (
            f"FAILED transition MUST leave HEAD byte-for-byte unchanged; "
            f"head={repo.head.commit.hexsha!r} seed={seed_head!r}"
        )
        post_staged_raw = must_str(repo.git.ls_files("--stage"))
        pre_staged_lines = sorted(pre_staged_raw.splitlines())
        post_staged_lines = sorted(post_staged_raw.splitlines())
        assert post_staged_lines == pre_staged_lines, (
            f"FAILED transition MUST restore the pre-staged index bytes; "
            f"pre={pre_staged_lines!r} post={post_staged_lines!r}"
        )
    finally:
        repo.close()


def test_dir_to_symlink_transition_does_not_commit_skipped_dirty_descendant(
    tmp_path: Path,
) -> None:
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
    pre_contents = capture_pre_write_contents(tmp_path, ["old", "old/clean", "old/wip"])

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
    assert "old/wip" in result.skipped_paths
    assert "old" in result.skipped_paths

    repo = Repo(tmp_path)
    try:
        assert repo.head.commit.hexsha == seed_head
        head_paths = {entry.path for entry in repo.head.commit.tree.traverse()}
        assert "old/wip" in head_paths
        assert "old/clean" in head_paths
        porcelain = repo.git.status("--porcelain")
        assert "old/wip" in porcelain
    finally:
        repo.close()


def test_transition_conflict_skips_only_the_conflicting_ancestor(
    tmp_path: Path,
) -> None:
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
    pre_contents = capture_pre_write_contents(tmp_path, ["old", "old/clean", "old/wip", "new.txt"])
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
        diff = repo.git.diff(seed_head, "HEAD", "--name-status")
        assert diff == "A\tnew.txt"
        head_paths = {entry.path for entry in repo.head.commit.tree.traverse()}
        assert "old/wip" in head_paths
        assert "old/clean" in head_paths
    finally:
        repo.close()


@pytest.mark.timeout_seconds(15)
def test_commit_does_not_misclassify_byte_equal_descendant_under_replaced_dir(
    tmp_path: Path,
) -> None:
    _init_repo_with_initial_commit(tmp_path)
    claude = tmp_path / ".claude" / "skills" / "foo"
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text("identical\n", encoding="utf-8")
    canonical = tmp_path / ".opencode" / "skills" / "foo"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text("identical\n", encoding="utf-8")

    repo = Repo(tmp_path)
    try:
        repo.index.add([".claude/skills/foo/SKILL.md"])
        repo.index.commit("add identical", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    finally:
        repo.close()

    pre_contents = capture_pre_write_contents(tmp_path, [".claude/skills/foo/SKILL.md"])
    assert pre_contents[".claude/skills/foo/SKILL.md"] is not None

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

    assert result.status is ScopedCommitStatus.CREATED, (
        f"byte-equal descendant under a replaced dir must commit; got: {result!r}"
    )

    repo = Repo(tmp_path)
    try:
        tree_paths = {entry.path for entry in repo.head.commit.tree}
        assert ".claude/skills/foo/SKILL.md" not in tree_paths
    finally:
        repo.close()


def test_ancestor_symlink_dirty_sentinel_is_distinguishable_from_real_sha() -> None:
    assert _ANCESTOR_SYMLINK_DIRTY != "f" * 40
    assert _ANCESTOR_SYMLINK_DIRTY.startswith("__")
    assert _ANCESTOR_SYMLINK_DIRTY.endswith("__")
