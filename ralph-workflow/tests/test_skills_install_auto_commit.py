from __future__ import annotations

import contextlib
import hashlib
import io
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from git import Actor, Repo
from rich.console import Console

from ralph.cli.commands import run as run_module
from ralph.display.context import make_display_context
from ralph.git.operations import create_commit, stage_files
from ralph.skills._auto_commit import (
    SKILL_AUTO_COMMIT_SUBJECT,
    ScopedCommitStatus,
    commit_skill_writes,
)
from ralph.skills._content import _MANAGED_MARKER, get_skill_content
from ralph.skills._installer import install_project_baseline_skills_with_diff

pytestmark = [
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(15),
]

_REPO_CACHE: dict[Path, Repo] = {}


def _repo(repo_root: Path, *, refresh: bool = False) -> Repo:
    if refresh:
        stale = _REPO_CACHE.pop(repo_root, None)
        if stale is not None:
            with contextlib.suppress(Exception):
                stale.close()
    repo = _REPO_CACHE.get(repo_root)
    if repo is None:
        repo = Repo(repo_root)
        _REPO_CACHE[repo_root] = repo
    return repo


@pytest.fixture(autouse=True)
def _close_cached_repositories() -> Iterator[None]:
    yield
    for repo in _REPO_CACHE.values():
        with contextlib.suppress(Exception):
            repo.close()
    _REPO_CACHE.clear()


@pytest.fixture(autouse=True)
def _scope_baseline_skills_to_test_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    test_skills = ("submit-artifact",)
    monkeypatch.setattr("ralph.skills._content.BASELINE_SKILL_NAMES", test_skills)
    monkeypatch.setattr("ralph.skills._installer.BASELINE_SKILL_NAMES", test_skills)
    monkeypatch.setattr("ralph.skills._installer_candidates.BASELINE_SKILL_NAMES", test_skills)
    monkeypatch.setattr("ralph.skills._prune.BASELINE_SKILL_NAMES", test_skills)


def _commit_all_initial(repo_root: Path) -> None:
    repo = _repo(repo_root, refresh=True)
    repo.config_writer().set_value("user", "name", "Test Author").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    actor = Actor("Test Author", "test@example.com")
    repo.index.commit("initial", author=actor, committer=actor)


def _head_tree_blob_paths(repo: Repo) -> set[str]:
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


def _write_managed_marker(skill_dir: Path, skill: str, **fields: object) -> None:
    marker = {"managed_by": "ralph-workflow", "skill": skill, **fields}
    (skill_dir / _MANAGED_MARKER).write_text(json.dumps(marker) + "\n", encoding="utf-8")


def _plant_managed_skill(root: Path, name: str, content: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    _write_managed_marker(skill_dir, name, files={"SKILL.md": digest})
    return skill_dir


def _plant_user_edited_skill(root: Path, name: str, installed: str, edited: str) -> Path:
    skill_dir = _plant_managed_skill(root, name, installed)
    (skill_dir / "SKILL.md").write_text(edited, encoding="utf-8")
    return skill_dir


def _plant_unmanaged_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# mine\n", encoding="utf-8")
    return skill_dir


def _commit_worktree(repo_root: Path, message: str) -> str:
    repo = _repo(repo_root, refresh=True)
    repo.git.add("-A")
    if not repo.is_dirty(untracked_files=True):
        return str(repo.head.commit.hexsha)
    actor = Actor("Test Author", "test@example.com")
    return str(repo.index.commit(message, author=actor, committer=actor).hexsha)


def test_install_replaces_tracked_sibling_dir_with_symlink_commits_deletions_and_link(
    tmp_path: Path,
) -> None:
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    sibling_dir = tmp_path / ".claude" / "skills" / "submit-artifact"
    sibling_dir.mkdir(parents=True)
    skill_md = sibling_dir / "SKILL.md"
    skill_md.write_text("old\n", encoding="utf-8")
    extra = sibling_dir / "extra.txt"
    extra.write_text("extra\n", encoding="utf-8")
    _write_managed_marker(
        sibling_dir,
        "submit-artifact",
        files={
            "SKILL.md": hashlib.sha256(b"old\n").hexdigest(),
            "extra.txt": hashlib.sha256(b"extra\n").hexdigest(),
        },
    )

    repo = Repo(tmp_path)
    try:
        repo.index.add(
            [
                ".claude/skills/submit-artifact/SKILL.md",
                ".claude/skills/submit-artifact/" + _MANAGED_MARKER,
                ".claude/skills/submit-artifact/extra.txt",
            ]
        )
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("seed tracked sibling dir", author=actor, committer=actor)
    finally:
        repo.close()

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)

    assert outcome.entry.status.value == "installed_healthy", (
        f"install must succeed; got entry={outcome.entry!r} failures={outcome.failures!r}"
    )
    assert outcome.failures == []

    expected_descendants = {
        ".claude/skills/submit-artifact",
        ".claude/skills/submit-artifact/SKILL.md",
        ".claude/skills/submit-artifact/" + _MANAGED_MARKER,
        ".claude/skills/submit-artifact/extra.txt",
    }
    actual_written = set(outcome.written_paths)
    for path in expected_descendants:
        assert path in actual_written, (
            f"U2 candidate-set expansion MUST include {path!r} so the "
            f"pre-write snapshot captures the install's deletion of "
            f"the tracked descendant or the new symlink; "
            f"written_paths={sorted(actual_written)}"
        )

    assert set(outcome.pre_contents.keys()) == actual_written, (
        "pre_contents keys MUST match written_paths exactly"
    )

    result = commit_skill_writes(
        tmp_path,
        written_paths=outcome.written_paths,
        pre_contents=outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
        intentional_transitions=outcome.intentional_transitions,
    )

    assert result.status is ScopedCommitStatus.CREATED, (
        f"U2: dir-to-symlink transition MUST commit; got: {result!r}"
    )
    assert result.sha is not None

    repo = Repo(tmp_path)
    try:
        head_subject = repo.head.commit.message.splitlines()[0]
        assert head_subject == SKILL_AUTO_COMMIT_SUBJECT, (
            f"chore commit subject MUST be deterministic; got: {head_subject!r}"
        )
        committed_paths = {
            diff.a_path for diff in repo.head.commit.diff(repo.head.commit.parents[0])
        }
        assert ".claude/skills/submit-artifact/SKILL.md" in committed_paths, (
            f"old SKILL.md MUST be in the chore commit's deletions; "
            f"got committed_paths={sorted(committed_paths)}"
        )
        assert ".claude/skills/submit-artifact/" + _MANAGED_MARKER in committed_paths, (
            f"old {_MANAGED_MARKER} MUST be in the chore commit's deletions; "
            f"got committed_paths={sorted(committed_paths)}"
        )
        assert ".claude/skills/submit-artifact/extra.txt" in committed_paths, (
            f"old extra.txt MUST be in the chore commit's deletions; "
            f"got committed_paths={sorted(committed_paths)}"
        )
        assert ".claude/skills/submit-artifact" in committed_paths, (
            f"new symlink at the sibling ROOT MUST land in the chore commit; "
            f"got committed_paths={sorted(committed_paths)}"
        )
        tree_paths = _head_tree_blob_paths(repo)
        assert ".claude/skills/submit-artifact/SKILL.md" not in tree_paths, (
            f"old SKILL.md MUST be gone from the HEAD tree; "
            f"got tree_paths={sorted(p for p in tree_paths if '.claude' in p)}"
        )
        assert ".claude/skills/submit-artifact/extra.txt" not in tree_paths, (
            f"old extra.txt MUST be gone from the HEAD tree; "
            f"got tree_paths={sorted(p for p in tree_paths if '.claude' in p)}"
        )
        assert ".claude/skills/submit-artifact/" + _MANAGED_MARKER not in tree_paths, (
            f"old {_MANAGED_MARKER} MUST be gone from the HEAD tree; "
            f"got tree_paths={sorted(p for p in tree_paths if '.claude' in p)}"
        )
        assert ".claude/skills/submit-artifact" in tree_paths, (
            f"new symlink at the sibling ROOT MUST land in the HEAD tree; "
            f"got tree_paths={sorted(p for p in tree_paths if '.claude' in p)}"
        )
        assert sibling_dir.is_symlink(), (
            f"sibling path MUST be a symlink after the install; "
            f"got is_symlink={sibling_dir.is_symlink()}"
        )
        assert (sibling_dir / "SKILL.md").read_text(encoding="utf-8") == get_skill_content(
            "submit-artifact"
        ), "SKILL.md via the new symlink MUST resolve to the canonical content"
        canonical_skill_md = tmp_path / ".opencode" / "skills" / "submit-artifact" / "SKILL.md"
        assert canonical_skill_md.read_text(encoding="utf-8") == get_skill_content(
            "submit-artifact"
        ), "canonical SKILL.md MUST be byte-identical to the bundled content"
    finally:
        repo.close()


def test_install_prunes_managed_skill_commits_deletions_and_sibling_symlink(
    tmp_path: Path,
) -> None:
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    retired = tmp_path / ".opencode" / "skills" / "retired-skill"
    retired.mkdir(parents=True)
    (retired / "SKILL.md").write_text("retired\n", encoding="utf-8")
    (retired / "extra.txt").write_text("retired extra\n", encoding="utf-8")
    (retired / _MANAGED_MARKER).write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": "retired-skill",
                "files": {
                    "SKILL.md": hashlib.sha256(b"retired\n").hexdigest(),
                    "extra.txt": hashlib.sha256(b"retired extra\n").hexdigest(),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

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

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)

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

    result = commit_skill_writes(
        tmp_path,
        written_paths=outcome.written_paths,
        pre_contents=outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
        intentional_transitions=outcome.intentional_transitions,
    )
    assert result.status is ScopedCommitStatus.CREATED, (
        f"U2: prune MUST commit them; got: {result!r}"
    )

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
        assert not retired.exists(), (
            f"retired canonical dir MUST be pruned from the working tree; "
            f"got exists={retired.exists()}"
        )
    finally:
        repo.close()


def test_project_level_removals_produce_exactly_one_chore_commit(
    tmp_path: Path,
) -> None:
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    retired = tmp_path / ".opencode" / "skills" / "retired-skill"
    retired.mkdir(parents=True)
    (retired / "SKILL.md").write_text("retired\n", encoding="utf-8")
    (retired / _MANAGED_MARKER).write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": "retired-skill",
                "installed_content_sha256": hashlib.sha256(b"retired\n").hexdigest(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    repo = Repo(tmp_path)
    try:
        repo.index.add(
            [
                ".opencode/skills/retired-skill/SKILL.md",
                ".opencode/skills/retired-skill/" + _MANAGED_MARKER,
            ]
        )
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("seed retired", author=actor, committer=actor)
        seed_head = repo.head.commit.hexsha
    finally:
        repo.close()

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)

    assert outcome.entry.status.value == "installed_healthy", outcome.failures
    assert not retired.exists(), "the retired managed skill must be pruned"

    result = commit_skill_writes(
        tmp_path,
        written_paths=outcome.written_paths,
        pre_contents=outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
        intentional_transitions=outcome.intentional_transitions,
    )
    assert result.status is ScopedCommitStatus.CREATED, result

    repo = Repo(tmp_path)
    try:
        new_commits = []
        for commit in repo.iter_commits():
            if commit.hexsha == seed_head:
                break
            new_commits.append(commit)
        assert len(new_commits) == 1, (
            f"the project-level removal MUST produce EXACTLY ONE commit; "
            f"got {len(new_commits)}: "
            f"{[(c.hexsha[:8], c.message.splitlines()[0]) for c in new_commits]}"
        )
        chore = new_commits[0]
        assert chore.message.splitlines()[0] == SKILL_AUTO_COMMIT_SUBJECT
        committed_paths = {diff.a_path for diff in chore.diff(chore.parents[0])}
        assert ".opencode/skills/retired-skill/SKILL.md" in committed_paths
        assert ".opencode/skills/retired-skill/" + _MANAGED_MARKER in committed_paths
        tree_paths = _head_tree_blob_paths(repo)
        assert ".opencode/skills/retired-skill/SKILL.md" not in tree_paths
    finally:
        repo.close()


def test_install_byte_equal_descendant_under_replaced_dir_still_commits(
    tmp_path: Path,
) -> None:
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    sibling_dir = tmp_path / ".claude" / "skills" / "submit-artifact"
    sibling_dir.mkdir(parents=True)
    byte_equal_content = get_skill_content("submit-artifact")
    (sibling_dir / "SKILL.md").write_text(byte_equal_content, encoding="utf-8")
    _write_managed_marker(
        sibling_dir,
        "submit-artifact",
        installed_content_sha256=hashlib.sha256(byte_equal_content.encode("utf-8")).hexdigest(),
    )

    repo = Repo(tmp_path)
    try:
        repo.index.add(
            [
                ".claude/skills/submit-artifact/SKILL.md",
                ".claude/skills/submit-artifact/" + _MANAGED_MARKER,
            ]
        )
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("seed byte-equal", author=actor, committer=actor)
    finally:
        repo.close()

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)

    actual_written = set(outcome.written_paths)
    assert ".claude/skills/submit-artifact/SKILL.md" in actual_written, (
        f"U2: byte-equal descendant MUST be in the candidate set; "
        f"got written_paths={sorted(actual_written)}"
    )
    assert ".claude/skills/submit-artifact" in actual_written, (
        f"U2: byte-equal sibling ROOT MUST be in the candidate set; "
        f"got written_paths={sorted(actual_written)}"
    )

    result = commit_skill_writes(
        tmp_path,
        written_paths=outcome.written_paths,
        pre_contents=outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
        intentional_transitions=outcome.intentional_transitions,
    )
    assert result.status is ScopedCommitStatus.CREATED, (
        f"U2: byte-equal descendant under a replaced dir MUST commit; got: {result!r}"
    )

    repo = Repo(tmp_path)
    try:
        tree_paths = {entry.path for entry in repo.head.commit.tree}
        assert ".claude/skills/submit-artifact/SKILL.md" not in tree_paths, (
            "byte-equal descendant MUST be deleted from HEAD tree"
        )
    finally:
        repo.close()


def test_install_unrelated_dirty_file_preserved(tmp_path: Path) -> None:
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    readme = tmp_path / "README.md"
    readme.write_text("user edit\n", encoding="utf-8")

    repo = Repo(tmp_path)
    try:
        actor = Actor("Test Author", "test@example.com")
        repo.index.add(["README.md"])
        repo.index.commit("seed README", author=actor, committer=actor)
        readme.write_text("user edit (dirty)\n", encoding="utf-8")
        repo.index.add(["README.md"])
    finally:
        repo.close()

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)

    assert "README.md" not in outcome.written_paths, (
        f"unrelated dirty file MUST NOT be in the install's written_paths; "
        f"got: {sorted(outcome.written_paths)}"
    )

    if outcome.written_paths:
        _ = commit_skill_writes(
            tmp_path,
            written_paths=outcome.written_paths,
            pre_contents=outcome.pre_contents,
            create_commit_fn=create_commit,
            stage_fn=stage_files,
            intentional_transitions=outcome.intentional_transitions,
        )

    committed_paths: set[str] = set()
    repo = Repo(tmp_path)
    try:
        if outcome.written_paths:
            for diff in repo.head.commit.diff(repo.head.commit.parents[0]):
                if diff.a_path is not None:
                    committed_paths.add(diff.a_path)
    finally:
        repo.close()

    assert "README.md" not in committed_paths, (
        f"unrelated dirty file MUST NOT be in the chore commit; "
        f"got committed_paths={sorted(committed_paths)}"
    )

    assert readme.read_text(encoding="utf-8") == "user edit (dirty)\n", (
        "unrelated dirty file MUST remain in its dirty state"
    )


def test_install_repeat_run_is_noop(tmp_path: Path) -> None:
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        first_outcome = install_project_baseline_skills_with_diff(tmp_path)

    assert first_outcome.written_paths, (
        "first install MUST commit the initial skill tree; got empty written_paths"
    )
    first_result = commit_skill_writes(
        tmp_path,
        written_paths=first_outcome.written_paths,
        pre_contents=first_outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
        intentional_transitions=first_outcome.intentional_transitions,
    )
    assert first_result.status is ScopedCommitStatus.CREATED, (
        f"first install MUST commit; got: {first_result!r}"
    )

    with patch("pathlib.Path.home", return_value=home):
        second_outcome = install_project_baseline_skills_with_diff(tmp_path)

    assert second_outcome.written_paths == [], (
        f"second install MUST produce empty written_paths; "
        f"got: {sorted(second_outcome.written_paths)}"
    )
    assert second_outcome.pre_contents == {}, (
        f"second install MUST produce empty pre_contents; got: {dict(second_outcome.pre_contents)}"
    )

    second_result = commit_skill_writes(
        tmp_path,
        written_paths=second_outcome.written_paths,
        pre_contents=second_outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
        intentional_transitions=second_outcome.intentional_transitions,
    )
    assert second_result.status is ScopedCommitStatus.NOOP, (
        f"second install MUST report NOOP (no spurious chore commit); got: {second_result!r}"
    )
    assert second_result.sha is None, f"NOOP result MUST have sha=None; got: {second_result.sha!r}"

    repo = Repo(tmp_path)
    try:
        assert repo.head.commit.hexsha == first_result.sha, (
            "second install MUST NOT advance HEAD; got a spurious commit"
        )
    finally:
        repo.close()


def test_install_symlink_unavailable_fallback_commits_materialized_leaves(
    tmp_path: Path,
) -> None:
    from git import Actor as _Actor

    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    def _raise_oserror(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("simulated: symlinks not supported on this filesystem")

    import ralph.skills._installer as _installer_module

    with patch.object(Path, "symlink_to", _raise_oserror):
        home = tmp_path / "fake-home"
        home.mkdir(parents=True, exist_ok=True)
        with patch("pathlib.Path.home", return_value=home):
            outcome = install_project_baseline_skills_with_diff(tmp_path)

    assert outcome.written_paths, (
        "fallback materialization MUST produce written_paths; got empty list"
    )
    sibling_roots = _installer_module.project_sibling_skill_roots(tmp_path)
    sibling_rel_roots = {
        s.resolve(tmp_path).relative_to(tmp_path).as_posix() for s in sibling_roots
    }
    for sibling_root in sibling_rel_roots:
        sibling_leaves = {p for p in outcome.written_paths if p.startswith(sibling_root + "/")}
        assert sibling_leaves, (
            f"sibling root {sibling_root!r} MUST contribute materialized leaves to "
            f"written_paths; got: {sorted(outcome.written_paths)}"
        )

    result = commit_skill_writes(
        tmp_path,
        written_paths=outcome.written_paths,
        pre_contents=outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
        intentional_transitions=outcome.intentional_transitions,
    )
    assert result.status is ScopedCommitStatus.CREATED, (
        f"fallback materialization MUST commit; got: {result!r}"
    )

    repo = Repo(tmp_path)
    try:
        porcelain = repo.git.status("--porcelain")
        untracked_sibling_lines = [
            line
            for line in porcelain.splitlines()
            if line.startswith("?? ")
            and any(
                line.endswith(sibling_root) or f"{sibling_root}/" in line
                for sibling_root in sibling_rel_roots
            )
        ]
        assert not untracked_sibling_lines, (
            f"fallback materialization MUST leave the sibling roots clean; "
            f"untracked lines: {untracked_sibling_lines!r}; full status: {porcelain!r}"
        )
        _ = _Actor
    finally:
        repo.close()


def test_install_skips_transition_with_pre_write_dirty_symlink_descendant(
    tmp_path: Path,
) -> None:
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    sibling_dir = tmp_path / ".claude" / "skills" / "submit-artifact"
    sibling_dir.mkdir(parents=True)
    original_file = sibling_dir / "original"
    original_file.write_text("original\n", encoding="utf-8")
    (sibling_dir / "alias").symlink_to("original")
    repo = Repo(tmp_path)
    try:
        repo.index.add(
            [
                ".claude/skills/submit-artifact/original",
                ".claude/skills/submit-artifact/alias",
            ]
        )
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("seed tracked symlink descendant", author=actor, committer=actor)
        seed_head = repo.head.commit.hexsha
        (sibling_dir / "alias").unlink()
        dirty_file = sibling_dir / "dirty"
        dirty_file.write_text("dirty\n", encoding="utf-8")
        (sibling_dir / "alias").symlink_to("dirty")
        orig_digest = hashlib.sha256(b"original\n").hexdigest()
        dirty_digest = hashlib.sha256(b"dirty\n").hexdigest()
        _write_managed_marker(
            sibling_dir,
            "submit-artifact",
            files={
                "original": orig_digest,
                "dirty": dirty_digest,
                "alias": dirty_digest,
            },
        )
    finally:
        repo.close()

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)

    assert ".claude/skills/submit-artifact/alias" in outcome.written_paths, (
        f"DA-010: the dirty symlink descendant must stay in written_paths; "
        f"got: {sorted(outcome.written_paths)}"
    )
    assert ".claude/skills/submit-artifact" not in outcome.intentional_transitions, (
        f"DA-003: a root with a pre-write-dirty descendant must NOT be an "
        f"intentional transition; got: {sorted(outcome.intentional_transitions)}"
    )

    result = commit_skill_writes(
        tmp_path,
        written_paths=outcome.written_paths,
        pre_contents=outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
        intentional_transitions=outcome.intentional_transitions,
    )

    assert ".claude/skills/submit-artifact/alias" in result.skipped_paths, (
        f"dirty descendant MUST be skipped; got: {result!r}"
    )
    assert ".claude/skills/submit-artifact" in result.skipped_paths, (
        f"conflicting transition root MUST be skipped; got: {result!r}"
    )

    repo = Repo(tmp_path)
    try:
        tree_paths = _head_tree_blob_paths(repo)
        assert ".claude/skills/submit-artifact/alias" in tree_paths, (
            f"user's original symlink descendant MUST stay tracked at HEAD; "
            f"got: {sorted(p for p in tree_paths if '.claude' in p)}"
        )
        assert ".claude/skills/submit-artifact" not in tree_paths, (
            f"skipped transition MUST NOT land the new symlink at HEAD; "
            f"got: {sorted(p for p in tree_paths if '.claude' in p)}"
        )
        for commit in repo.iter_commits():
            if commit.hexsha == seed_head:
                break
            deleted = {diff.a_path for diff in commit.diff(commit.parents[0]) if diff.deleted_file}
            assert ".claude/skills/submit-artifact/alias" not in deleted, (
                f"chore commit {commit.hexsha[:8]} MUST NOT sweep the dirty "
                f"descendant's deletion; deleted={sorted(deleted)}"
            )
        porcelain = repo.git.status("--porcelain")
        assert any(
            ".claude/skills/submit-artifact/alias" in line for line in porcelain.splitlines()
        ), f"user's dirty descendant MUST remain uncommitted; status: {porcelain!r}"
    finally:
        repo.close()


def test_run_start_leaves_a_user_edited_retired_skill_alone_without_a_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_DISABLE_SKILL_UPDATES", "1")
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_gitignore", lambda _root: [])
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_git_exclude", lambda _root: [])

    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)

    with patch("pathlib.Path.home", return_value=home):
        run_module.sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    _commit_worktree(tmp_path, "install baseline skills")

    canonical = tmp_path / ".opencode" / "skills"
    _plant_user_edited_skill(canonical, "edited-retired-skill", "installed\n", "# edited\n")
    _plant_unmanaged_skill(canonical, "user-retired-skill")
    seed_head = _commit_worktree(tmp_path, "seed edited retired skills")

    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None)
    display_context = make_display_context(console=console)
    monkeypatch.setattr(run_module, "make_display_context", lambda **_kwargs: display_context)

    with patch("pathlib.Path.home", return_value=home):
        run_module.sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    rendered = stream.getvalue()
    assert "skills-prune-preserved-edited-retired-skill" not in rendered, (
        f"a USER-EDITED retired skill alone MUST NOT enter the project install; "
        f"the preserved entry would re-run the install on every launch; got: {rendered!r}"
    )
    assert (canonical / "edited-retired-skill" / "SKILL.md").read_text(encoding="utf-8") == (
        "# edited\n"
    ), "the USER-EDITED retired skill MUST survive the run"
    assert (canonical / "user-retired-skill" / "SKILL.md").read_text(encoding="utf-8") == (
        "# mine\n"
    ), "the USER-OWNED retired skill MUST survive the run"

    repo = Repo(tmp_path)
    try:
        new_commits = []
        for commit in repo.iter_commits():
            if commit.hexsha == seed_head:
                break
            new_commits.append(commit)
        assert new_commits == [], (
            f"a USER-EDITED retired skill alone MUST NOT produce a commit; "
            f"got {[(c.hexsha[:8], c.message.splitlines()[0]) for c in new_commits]}"
        )
    finally:
        repo.close()


def test_run_start_migration_removes_retired_managed_skills_in_one_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_DISABLE_SKILL_UPDATES", "1")
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_gitignore", lambda _root: [])
    monkeypatch.setattr("ralph.config.bootstrap.auto_seed_default_git_exclude", lambda _root: [])

    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)

    with patch("pathlib.Path.home", return_value=home):
        run_module.sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    _commit_worktree(tmp_path, "install baseline skills")

    canonical_root = tmp_path / ".opencode" / "skills"
    sibling_root = tmp_path / ".claude" / "skills"
    user_global_root = home / ".claude" / "skills"

    for root in (canonical_root, sibling_root, user_global_root):
        _plant_managed_skill(root, "retired-skill", "retired\n")
        _plant_user_edited_skill(root, "edited-skill", "installed\n", "# edited\n")
        _plant_unmanaged_skill(root, "user-skill")

    retired_link = tmp_path / ".codex" / "skills" / "retired-skill"
    retired_link.parent.mkdir(parents=True, exist_ok=True)
    retired_link.symlink_to(Path("../../.opencode/skills/retired-skill"), target_is_directory=True)

    seed_head = _commit_worktree(tmp_path, "seed retired skills")

    with patch("pathlib.Path.home", return_value=home):
        run_module.sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    assert not (canonical_root / "retired-skill").exists(), (
        "the retired MANAGED skill MUST be removed from the project canonical root"
    )
    assert not (sibling_root / "retired-skill").exists(), (
        "the retired MANAGED skill MUST be removed from the project sibling root"
    )
    assert not (user_global_root / "retired-skill").exists(), (
        "the retired MANAGED skill MUST be removed from the user-global root"
    )
    assert not retired_link.is_symlink(), (
        "the retired sibling symlink MUST be removed with the entry it pointed at"
    )

    for root in (canonical_root, sibling_root, user_global_root):
        assert (root / "user-skill" / "SKILL.md").read_text(encoding="utf-8") == "# mine\n", (
            f"the USER-OWNED skill MUST survive in {root}"
        )
        assert (root / "edited-skill" / "SKILL.md").read_text(encoding="utf-8") == "# edited\n", (
            f"the USER-EDITED skill MUST survive in {root}"
        )

    expected_retired_paths = {
        ".opencode/skills/retired-skill/SKILL.md",
        ".opencode/skills/retired-skill/" + _MANAGED_MARKER,
        ".claude/skills/retired-skill/SKILL.md",
        ".claude/skills/retired-skill/" + _MANAGED_MARKER,
        ".codex/skills/retired-skill",
    }

    repo = Repo(tmp_path)
    try:
        new_commits = []
        for commit in repo.iter_commits():
            if commit.hexsha == seed_head:
                break
            new_commits.append(commit)
        assert len(new_commits) == 1, (
            f"the migration MUST produce EXACTLY ONE commit; got {len(new_commits)}: "
            f"{[(c.hexsha[:8], c.message.splitlines()[0]) for c in new_commits]}"
        )
        chore = new_commits[0]
        assert chore.message.splitlines()[0] == SKILL_AUTO_COMMIT_SUBJECT, (
            f"the migration commit subject MUST be deterministic; got: "
            f"{chore.message.splitlines()[0]!r}"
        )
        committed_paths = {diff.a_path for diff in chore.diff(chore.parents[0])}
        assert committed_paths == expected_retired_paths, (
            f"the migration commit MUST touch ONLY the retired skill paths; "
            f"got: {sorted(committed_paths)}"
        )
    finally:
        repo.close()


def test_install_preserved_conflicting_skill_dir_is_not_swept_into_chore_commit(
    tmp_path: Path,
) -> None:
    Repo.init(tmp_path)
    _commit_all_initial(tmp_path)

    sibling_dir = tmp_path / ".claude" / "skills" / "submit-artifact"
    sibling_dir.mkdir(parents=True)
    _write_managed_marker(sibling_dir, "submit-artifact", files={})
    (sibling_dir / "alias").symlink_to("original")
    (sibling_dir / "notes.md").write_text("user notes\n", encoding="utf-8")
    repo = Repo(tmp_path)
    try:
        repo.index.add([".claude/skills/submit-artifact/alias"])
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("seed tracked symlink descendant", author=actor, committer=actor)
        (sibling_dir / "alias").unlink()
        (sibling_dir / "alias").symlink_to("dirty")
    finally:
        repo.close()

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)

    assert ".claude/skills/submit-artifact/alias" in outcome.written_paths, (
        f"the dirty symlink descendant MUST be reported; got: {sorted(outcome.written_paths)}"
    )
    assert ".claude/skills/submit-artifact" in outcome.written_paths, (
        f"the conflicting transition root MUST be reported; got: {sorted(outcome.written_paths)}"
    )

    result = commit_skill_writes(
        tmp_path,
        written_paths=outcome.written_paths,
        pre_contents=outcome.pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
        intentional_transitions=outcome.intentional_transitions,
    )

    assert ".claude/skills/submit-artifact" in result.skipped_paths, (
        f"conflicting transition root MUST be skipped; got: {result!r}"
    )

    repo = Repo(tmp_path)
    try:
        tree_paths = _head_tree_blob_paths(repo)
        assert ".claude/skills/submit-artifact/notes.md" not in tree_paths, (
            f"the user's untracked file MUST NOT be swept into the chore commit; "
            f"got: {sorted(p for p in tree_paths if '.claude' in p)}"
        )
        assert ".claude/skills/submit-artifact" not in tree_paths, (
            f"the skipped transition MUST NOT land a symlink at HEAD; "
            f"got: {sorted(p for p in tree_paths if '.claude' in p)}"
        )
        porcelain = repo.git.status("--porcelain")
        assert any(
            ".claude/skills/submit-artifact/notes.md" in line for line in porcelain.splitlines()
        ), f"the user's untracked file MUST stay untracked; status: {porcelain!r}"
    finally:
        repo.close()
