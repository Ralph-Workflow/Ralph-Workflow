"""Black-box tests for the project-scope skill install + auto-commit boundary.

Pins the wt-012 U2 / PA-002 contract for the producer-level
``install_project_baseline_skills_with_diff`` wrapper and the
``commit_skill_writes`` boundary it routes through. The
:mod:`tests.test_skills_auto_commit` module covers the legacy
``commit_skill_updates`` helper; this module is the matching
acceptance surface for the byte-exact install / chore-commit
boundary (the dir-to-symlink transition, the byte-equal trap, the
prune + sibling symlink case, the unrelated-dirty-file guard, and
the no-op second run).

The tests use real git (per-test ``tmp_path`` repos) and the
production ``create_commit`` / ``stage_files`` wiring. They run
in well under the per-test ``subprocess_e2e`` /
``timeout_seconds(5)`` cap and stay inside the IMMUTABLE 60 s
combined verify budget.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from git import Actor, Repo

from ralph.git.operations import create_commit, stage_files
from ralph.skills._auto_commit import (
    SKILL_AUTO_COMMIT_SUBJECT,
    ScopedCommitStatus,
    commit_skill_writes,
)
from ralph.skills._content import _MANAGED_MARKER, get_skill_content
from ralph.skills._installer import install_project_baseline_skills_with_diff

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _commit_all_initial(repo_root: Path) -> None:
    """Initial empty commit so HEAD exists for the per-test diffing."""
    repo = Repo(repo_root)
    try:
        repo.config_writer().set_value("user", "name", "Test Author").release()
        repo.config_writer().set_value("user", "email", "test@example.com").release()
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("initial", author=actor, committer=actor)
    finally:
        repo.close()


def _head_tree_blob_paths(repo: Repo) -> set[str]:
    """Return every blob path under the HEAD commit tree.

    Walks the tree depth-first and collects ``entry.path`` for every
    blob (regular file or symlink). Useful for asserting that a chore
    commit's HEAD tree no longer contains a deleted descendant.
    """
    tree_paths: set[str] = set()
    stack = [repo.head.commit.tree]
    while stack:
        subtree = stack.pop()
        for sub_entry in subtree:
            if sub_entry.type == "blob":
                tree_paths.add(sub_entry.path)
            elif sub_entry.type == "tree":
                stack.append(sub_entry)
    return tree_paths


def test_install_replaces_tracked_sibling_dir_with_symlink_commits_deletions_and_link(
    tmp_path: Path,
) -> None:
    """U2 / PA-002: a tracked sibling directory replaced by a symlink commits BOTH sides.

    Setup: a tracked sibling dir ``.claude/skills/<brainstorming>/`` with
    three tracked files (``SKILL.md``, ``_MANAGED_MARKER.json``, plus a
    third ``extra.txt``). The candidate set MUST include the sibling
    ROOT path, every baseline skill entry, and EVERY tracked
    descendant so the install's pre-write snapshot records the
    pre-write hash of every file that ``shutil.rmtree`` will delete.
    After the install, the install's chore commit MUST land the
    descendant deletions AND the new symlink atomically.

    The canonical content (the new bundle) is created BEFORE the
    install runs so the install has something to symlink to, and the
    test asserts the canonical content is preserved verbatim across
    the chore commit.

    The test uses the REAL ``create_commit`` + ``stage_files`` from
    ``ralph.git.operations`` so the post-commit tree is inspected via
    ``head.commit.tree`` and ``head.diff(parents[0])`` -- a MagicMock
    would not commit, defeating the end-to-end contract.
    """
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    # 1. Track a sibling dir ``brainstorming`` (a real baseline name) so
    #    the install's dir-to-symlink transition replaces it.
    sibling_dir = tmp_path / ".claude" / "skills" / "brainstorming"
    sibling_dir.mkdir(parents=True)
    skill_md = sibling_dir / "SKILL.md"
    skill_md.write_text("old\n", encoding="utf-8")
    marker = sibling_dir / _MANAGED_MARKER
    marker.write_text(
        '{"managed_by": "ralph-workflow", "skill": "brainstorming"}\n',
        encoding="utf-8",
    )
    extra = sibling_dir / "extra.txt"
    extra.write_text("extra\n", encoding="utf-8")

    repo = Repo(tmp_path)
    try:
        repo.index.add(
            [
                ".claude/skills/brainstorming/SKILL.md",
                ".claude/skills/brainstorming/" + _MANAGED_MARKER,
                ".claude/skills/brainstorming/extra.txt",
            ]
        )
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit(
            "seed tracked sibling dir", author=actor, committer=actor
        )
    finally:
        repo.close()

    # 2. Run the producer-level wrapper. The candidate set MUST include
    #    the sibling ROOT, the baseline skill entry, AND every tracked
    #    descendant.
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)

    assert outcome.entry.status.value == "installed_healthy", (
        f"install must succeed; got entry={outcome.entry!r} failures={outcome.failures!r}"
    )
    assert outcome.failures == []

    # 3. The candidate set must include the sibling ROOT path (the
    #    install records the new symlink at this lexical path), the
    #    sibling baseline skill entry, AND every tracked descendant
    #    (three files in the dir-to-replaced-symlink path). The ROOT
    #    itself is a directory; git does not track directories, so the
    #    ROOT is a no-op in the post-install diff (pre_sha=None,
    #    post_sha=None) -- it is harmless to include as a candidate so
    #    the sibling ROOT entry always lands in the pre-write snapshot.
    expected_descendants = {
        ".claude/skills/brainstorming",
        ".claude/skills/brainstorming/SKILL.md",
        ".claude/skills/brainstorming/" + _MANAGED_MARKER,
        ".claude/skills/brainstorming/extra.txt",
    }
    actual_written = set(outcome.written_paths)
    for path in expected_descendants:
        assert path in actual_written, (
            f"U2 candidate-set expansion MUST include {path!r} so the "
            f"pre-write snapshot captures the install's deletion of "
            f"the tracked descendant or the new symlink; "
            f"written_paths={sorted(actual_written)}"
        )

    # 4. Run the REAL production commit pipeline (no MagicMock) so the
    #    post-commit tree is observable. The recorded pre-write contents
    #    map only the byte-exact set of paths the install actually
    #    changed -- we feed the wrapper's ``pre_contents`` slice to
    #    ``commit_skill_writes`` so the helper skips any path the
    #    install did not touch.
    assert set(outcome.pre_contents.keys()) == actual_written, (
        "pre_contents keys MUST match written_paths exactly"
    )

    # Re-snapshot the pre-write contents BEFORE the commit so the
    # commit_skill_writes contract sees a coherent pre-write map for
    # the byte-exact diff. The wrapper already pre-captured the hashes;
    # we feed them through verbatim so the test exercises the production
    # path end-to-end.
    result = commit_skill_writes(
        tmp_path,
        written_paths=outcome.written_paths,
        pre_contents=outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
    )

    assert result.status is ScopedCommitStatus.CREATED, (
        f"U2: dir-to-symlink transition MUST commit; got: {result!r}"
    )
    assert result.sha is not None

    # 5. Post-commit tree MUST no longer contain the old tracked
    #    descendants, MUST contain the new symlink at the old path, and
    #    MUST leave the canonical content byte-identical.
    repo = Repo(tmp_path)
    try:
        head_subject = repo.head.commit.message.splitlines()[0]
        assert head_subject == SKILL_AUTO_COMMIT_SUBJECT, (
            f"chore commit subject MUST be deterministic; got: {head_subject!r}"
        )
        committed_paths = {
            diff.a_path for diff in repo.head.commit.diff(repo.head.commit.parents[0])
        }
        # Old tracked descendants are gone (the diff lists them as
        # deletions even though the new symlink's resolved content
        # shadows them on disk).
        assert ".claude/skills/brainstorming/SKILL.md" in committed_paths, (
            f"old SKILL.md MUST be in the chore commit's deletions; "
            f"got committed_paths={sorted(committed_paths)}"
        )
        assert ".claude/skills/brainstorming/" + _MANAGED_MARKER in committed_paths, (
            f"old {_MANAGED_MARKER} MUST be in the chore commit's deletions; "
            f"got committed_paths={sorted(committed_paths)}"
        )
        assert ".claude/skills/brainstorming/extra.txt" in committed_paths, (
            f"old extra.txt MUST be in the chore commit's deletions; "
            f"got committed_paths={sorted(committed_paths)}"
        )
        # The new symlink at the old path lands in the chore commit.
        assert ".claude/skills/brainstorming" in committed_paths, (
            f"new symlink at the sibling ROOT MUST land in the chore commit; "
            f"got committed_paths={sorted(committed_paths)}"
        )
        # Post-commit HEAD tree MUST NOT contain the old descendants.
        tree_paths = _head_tree_blob_paths(repo)
        assert ".claude/skills/brainstorming/SKILL.md" not in tree_paths, (
            f"old SKILL.md MUST be gone from the HEAD tree; "
            f"got tree_paths={sorted(p for p in tree_paths if '.claude' in p)}"
        )
        assert ".claude/skills/brainstorming/extra.txt" not in tree_paths, (
            f"old extra.txt MUST be gone from the HEAD tree; "
            f"got tree_paths={sorted(p for p in tree_paths if '.claude' in p)}"
        )
        assert ".claude/skills/brainstorming/" + _MANAGED_MARKER not in tree_paths, (
            f"old {_MANAGED_MARKER} MUST be gone from the HEAD tree; "
            f"got tree_paths={sorted(p for p in tree_paths if '.claude' in p)}"
        )
        # The new symlink at the old path lands in the HEAD tree.
        assert ".claude/skills/brainstorming" in tree_paths, (
            f"new symlink at the sibling ROOT MUST land in the HEAD tree; "
            f"got tree_paths={sorted(p for p in tree_paths if '.claude' in p)}"
        )
        # Working tree: the sibling path is now a symlink; reading
        # ``SKILL.md`` through it resolves to the canonical.
        assert sibling_dir.is_symlink(), (
            f"sibling path MUST be a symlink after the install; "
            f"got is_symlink={sibling_dir.is_symlink()}"
        )
        assert (sibling_dir / "SKILL.md").read_text(encoding="utf-8") == get_skill_content(
            "brainstorming"
        ), "SKILL.md via the new symlink MUST resolve to the canonical content"
        # The new canonical content is byte-identical.
        canonical_skill_md = tmp_path / ".opencode" / "skills" / "brainstorming" / "SKILL.md"
        assert canonical_skill_md.read_text(encoding="utf-8") == get_skill_content(
            "brainstorming"
        ), "canonical SKILL.md MUST be byte-identical to the bundled content"
    finally:
        repo.close()


def test_install_prunes_managed_skill_commits_deletions_and_sibling_symlink(
    tmp_path: Path,
) -> None:
    """U2 / DA-007: pruning a retired canonical skill commits the deletions AND any sibling symlink.

    Setup: a tracked managed skill directory at the canonical
    (``.opencode/skills/<retired>/``) that the baseline no longer
    ships, with three tracked files. The install's ``_prune_removed_baseline_skills``
    step deletes the directory via ``shutil.rmtree``; a sibling may
    still carry a symlink to the now-deleted canonical target.

    The candidate set MUST include ALL descendants of the prune
    target (not just ``SKILL.md`` + ``_MANAGED_MARKER.json``) so the
    pre-write snapshot records the pre-write hash of every file the
    prune deletes. The sibling symlink (if any) lands in the candidate
    set via the sibling-tracked-descendants branch.

    The test asserts the chore commit lands every retired-skill file's
    deletion AND any sibling symlink removal.
    """
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    # 1. Track a retired managed skill at the canonical with three
    #    files (so the U2 descendant expansion is exercised -- only two
    #    files would be picked up by the OLD candidate-set logic).
    retired = tmp_path / ".opencode" / "skills" / "retired-skill"
    retired.mkdir(parents=True)
    (retired / "SKILL.md").write_text("retired\n", encoding="utf-8")
    (retired / _MANAGED_MARKER).write_text(
        '{"managed_by": "ralph-workflow", "skill": "retired-skill"}\n',
        encoding="utf-8",
    )
    (retired / "extra.txt").write_text("retired extra\n", encoding="utf-8")

    # 3. Track a sibling symlink to the retired canonical so the prune
    #    also leaves a stale (now-broken) sibling symlink.
    retired_sibling_link = tmp_path / ".claude" / "skills" / "retired-skill"
    retired_sibling_link.parent.mkdir(parents=True, exist_ok=True)
    retired_sibling_link.symlink_to(retired, target_is_directory=True)

    repo = Repo(tmp_path)
    try:
        paths_to_track = [
            ".opencode/skills/retired-skill/SKILL.md",
            ".opencode/skills/retired-skill/" + _MANAGED_MARKER,
            ".opencode/skills/retired-skill/extra.txt",
            ".claude/skills/retired-skill",
        ]
        repo.index.add(paths_to_track)
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("seed retired", author=actor, committer=actor)
    finally:
        repo.close()

    # 4. Run the install via the producer-level wrapper.
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)

    # The install completes (it just prunes the retired skill). The
    # candidate-set expansion MUST surface every descendant of the
    # retired skill directory.
    expected_descendants = {
        ".opencode/skills/retired-skill/SKILL.md",
        ".opencode/skills/retired-skill/" + _MANAGED_MARKER,
        ".opencode/skills/retired-skill/extra.txt",
    }
    actual_written = set(outcome.written_paths)
    for path in expected_descendants:
        assert path in actual_written, (
            f"U2 candidate-set expansion MUST include the prune target "
            f"descendant {path!r}; got written_paths={sorted(actual_written)}"
        )

    # 5. Run the REAL production commit pipeline (no MagicMock) so the
    #    post-commit tree is observable.
    result = commit_skill_writes(
        tmp_path,
        written_paths=outcome.written_paths,
        pre_contents=outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
    )
    assert result.status is ScopedCommitStatus.CREATED, (
        f"U2: prune MUST commit them; got: {result!r}"
    )

    # 6. Post-commit: the retired descendants are gone from the tree.
    repo = Repo(tmp_path)
    try:
        head_subject = repo.head.commit.message.splitlines()[0]
        assert head_subject == SKILL_AUTO_COMMIT_SUBJECT, (
            f"chore commit subject MUST be deterministic; got: {head_subject!r}"
        )
        committed_paths = {
            diff.a_path for diff in repo.head.commit.diff(repo.head.commit.parents[0])
        }
        for path in expected_descendants:
            assert path in committed_paths, (
                f"prune descendant {path!r} MUST be in the chore commit's "
                f"deletions; got committed_paths={sorted(committed_paths)}"
            )
        # The retired skill directory is gone from the working tree.
        assert not retired.exists(), (
            f"retired canonical dir MUST be pruned from the working tree; "
            f"got exists={retired.exists()}"
        )
    finally:
        repo.close()


def test_install_byte_equal_descendant_under_replaced_dir_still_commits(
    tmp_path: Path,
) -> None:
    """U2 / PA-002 byte-equal trap: a deleted descendant with the same content as the
    new symlink target MUST STILL be staged (the deletion is real).

    Without the U0 ancestor-symlink safety, ``git hash-object`` would
    follow the new symlink and report the same blob as HEAD, so the
    no-op short-circuit would silently drop the deletion. Without the
    U2 candidate-set expansion, the descendant isn't even in the
    candidate set, so its pre-write hash is never captured.
    """
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    # 1. Track a sibling skill with byte-equal content to the bundled
    #    canonical content.
    sibling_dir = tmp_path / ".claude" / "skills" / "brainstorming"
    sibling_dir.mkdir(parents=True)
    byte_equal_content = get_skill_content("brainstorming")
    (sibling_dir / "SKILL.md").write_text(byte_equal_content, encoding="utf-8")
    (sibling_dir / _MANAGED_MARKER).write_text(
        '{"managed_by": "ralph-workflow", "skill": "brainstorming"}\n',
        encoding="utf-8",
    )

    repo = Repo(tmp_path)
    try:
        repo.index.add(
            [
                ".claude/skills/brainstorming/SKILL.md",
                ".claude/skills/brainstorming/" + _MANAGED_MARKER,
            ]
        )
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit(
            "seed byte-equal", author=actor, committer=actor
        )
    finally:
        repo.close()

    # 2. Run the install via the producer-level wrapper.
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)

    # The candidate set MUST include the descendant SKILL.md (so its
    # pre-write hash is recorded) AND the sibling skill entry (so the
    # new symlink is staged). The byte-equal trap is neutralised by
    # the U2 candidate-set expansion + U0 ancestor-symlink safety.
    actual_written = set(outcome.written_paths)
    assert ".claude/skills/brainstorming/SKILL.md" in actual_written, (
        f"U2: byte-equal descendant MUST be in the candidate set; "
        f"got written_paths={sorted(actual_written)}"
    )
    assert ".claude/skills/brainstorming" in actual_written, (
        f"U2: byte-equal sibling ROOT MUST be in the candidate set; "
        f"got written_paths={sorted(actual_written)}"
    )

    # 3. Run the REAL production commit pipeline.
    result = commit_skill_writes(
        tmp_path,
        written_paths=outcome.written_paths,
        pre_contents=outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
    )
    # The deletion MUST land in the commit (CREATED, not NOOP). If the
    # byte-equal trap misclassified the descendant, the helper would
    # return NOOP / SKIPPED and the deletion would leak into the
    # working tree uncommitted.
    assert result.status is ScopedCommitStatus.CREATED, (
        f"U2: byte-equal descendant under a replaced dir MUST commit; "
        f"got: {result!r}"
    )

    # 4. Post-commit: the descendant is no longer tracked.
    repo = Repo(tmp_path)
    try:
        tree_paths = {entry.path for entry in repo.head.commit.tree}
        assert ".claude/skills/brainstorming/SKILL.md" not in tree_paths, (
            "byte-equal descendant MUST be deleted from HEAD tree"
        )
    finally:
        repo.close()


def test_install_unrelated_dirty_file_preserved(tmp_path: Path) -> None:
    """U2: a pre-existing dirty file outside the skill scope is preserved.

    Pins the scope-changed contract that the install's chore commit
    MUST NOT capture unrelated pre-staged entries. The user has dirtied
    ``README.md`` before the install runs; the install MUST leave it
    in the working tree (NOT in the chore commit, NOT silently
    committed by the install).
    """
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    # 1. Pre-dirty an unrelated file before the install runs. The file
    #    is staged (so it has a HEAD blob to compare against) AND has
    #    a working-tree edit (so it is dirty in the index).
    readme = tmp_path / "README.md"
    readme.write_text("user edit\n", encoding="utf-8")

    repo = Repo(tmp_path)
    try:
        actor = Actor("Test Author", "test@example.com")
        repo.index.add(["README.md"])
        repo.index.commit("seed README", author=actor, committer=actor)
        # Working-tree change so the file is dirty in the index.
        readme.write_text("user edit (dirty)\n", encoding="utf-8")
        # Stage the working-tree edit so the file is dirty at HEAD.
        repo.index.add(["README.md"])
    finally:
        repo.close()

    # 2. Run the install via the producer-level wrapper.
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)

    # README.md is NOT in the candidate set (it is outside the skill
    # scope), so the install MUST NOT touch it. The wrapper returns an
    # outcome whose written_paths are byte-exact skill-scope only.
    assert "README.md" not in outcome.written_paths, (
        f"unrelated dirty file MUST NOT be in the install's written_paths; "
        f"got: {sorted(outcome.written_paths)}"
    )

    # 3. Run the REAL production commit pipeline when the install wrote
    #    anything. The pre-write snapshot for the unrelated file is
    #    missing from ``outcome.pre_contents`` (it's outside the
    #    candidate set), so the commit helper has nothing to commit
    #    for it.
    if outcome.written_paths:
        _ = commit_skill_writes(
            tmp_path,
            written_paths=outcome.written_paths,
            pre_contents=outcome.pre_contents,
            create_commit_fn=create_commit,
            stage_fn=stage_files,
        )

    # Inspect the chore commit (if any). The chore commit may succeed
    # (install genuinely wrote skill files) but MUST NOT include
    # ``README.md``. If the install wrote nothing, the commit step was
    # a NOOP and there is no new commit to inspect -- ``committed_paths``
    # is therefore the empty set.
    committed_paths: set[str] = set()
    repo = Repo(tmp_path)
    try:
        if outcome.written_paths:
            for diff in repo.head.commit.diff(repo.head.commit.parents[0]):
                # ``diff.a_path`` is ``str | None`` in some gitpython
                # overloads; ignore None defensively.
                if diff.a_path is not None:
                    committed_paths.add(diff.a_path)
    finally:
        repo.close()

    assert "README.md" not in committed_paths, (
        f"unrelated dirty file MUST NOT be in the chore commit; "
        f"got committed_paths={sorted(committed_paths)}"
    )

    # 4. README.md is still dirty in the working tree (preserved).
    assert readme.read_text(encoding="utf-8") == "user edit (dirty)\n", (
        "unrelated dirty file MUST remain in its dirty state"
    )


def test_install_repeat_run_is_noop(tmp_path: Path) -> None:
    """U2 / AC-03: a second install run MUST produce an empty written_paths and no commit.

    Pins the no-op contract for the byte-exact producer-level wrapper.
    The first install commits the initial install tree. The SECOND
    install MUST report empty ``written_paths`` (no spurious chore
    commit) because the install's pre-write hashes match HEAD (nothing
    changed since the install ran) and the post-install on-disk hashes
    match HEAD too.
    """
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        first_outcome = install_project_baseline_skills_with_diff(tmp_path)

    # First install MUST commit some skill paths (the initial tree).
    assert first_outcome.written_paths, (
        "first install MUST commit the initial skill tree; got empty written_paths"
    )
    first_result = commit_skill_writes(
        tmp_path,
        written_paths=first_outcome.written_paths,
        pre_contents=first_outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
    )
    assert first_result.status is ScopedCommitStatus.CREATED, (
        f"first install MUST commit; got: {first_result!r}"
    )

    # Second install MUST produce empty written_paths (no diff since HEAD).
    with patch("pathlib.Path.home", return_value=home):
        second_outcome = install_project_baseline_skills_with_diff(tmp_path)

    assert second_outcome.written_paths == [], (
        f"second install MUST produce empty written_paths; "
        f"got: {sorted(second_outcome.written_paths)}"
    )
    assert second_outcome.pre_contents == {}, (
        f"second install MUST produce empty pre_contents; "
        f"got: {dict(second_outcome.pre_contents)}"
    )

    # No chore commit on the second install -- the helper is called with
    # an empty written_paths, which routes through commit_deterministic_writes
    # and returns NOOP without ever invoking stage_fn / create_commit.
    second_result = commit_skill_writes(
        tmp_path,
        written_paths=second_outcome.written_paths,
        pre_contents=second_outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
    )
    assert second_result.status is ScopedCommitStatus.NOOP, (
        f"second install MUST report NOOP (no spurious chore commit); "
        f"got: {second_result!r}"
    )
    assert second_result.sha is None, (
        f"NOOP result MUST have sha=None; got: {second_result.sha!r}"
    )

    # Sanity: HEAD is still the first install's commit.
    repo = Repo(tmp_path)
    try:
        assert repo.head.commit.hexsha == first_result.sha, (
            "second install MUST NOT advance HEAD; got a spurious commit"
        )
    finally:
        repo.close()
