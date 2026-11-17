from __future__ import annotations

import functools
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from git import Repo

from ralph.agents.timeout_clock import FakeClock
from tests._scenario_harness import AgentMode, FakeAgentRunner
from tests.scenarios._content_equivalence import (
    assert_content_equivalence,
    drive_integration,
)

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]

LANDED = "the feature's content must be present on the target branch"

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "relation_topology_template_reuse": (
        "each relation topology is built once and hardlink-copytree'd per case"
    ),
    "FakeClock_on_integrate_drives": (
        "relation integrate drives use FakeClock; landing asserts unchanged"
    ),
    "GitPython_seed_and_sha_reads": (
        "topology seeds and tip reads use GitPython instead of git subprocess helpers"
    ),
}


def _link_git_objects_or_copy(src: str, dst: str) -> None:
    src_path = Path(src)
    parts = src_path.parts
    if ".git" in parts:
        git_index = parts.index(".git")
        rest = parts[git_index + 1 :]
        if rest and rest[0] == "objects" and len(rest) >= 2:
            try:
                os.link(src, dst)
            except OSError:
                pass
            else:
                return
    shutil.copy2(src, dst)


def _seed_repo(base: Path) -> Repo:
    readme = base / "README.md"
    readme.write_text("seed\n", encoding="utf-8")
    repo = Repo.init(base, initial_branch="main")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("commit", "gpgsign", "false")
    repo.index.add(["README.md"])
    repo.index.commit("seed")
    return repo


def _write_commit(repo: Repo, relative: str, content: str, message: str) -> str:
    root = Path(repo.working_tree_dir or ".")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relative])
    return repo.index.commit(message).hexsha


def _materialize(template: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        template,
        dest,
        copy_function=_link_git_objects_or_copy,
        ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
    )
    return dest


def _sha(root: Path, ref: str) -> str:
    repo = Repo(root)
    try:
        return repo.commit(ref).hexsha
    finally:
        repo.close()


def _drive(root: Path, *, resolver: FakeAgentRunner | None = None) -> object:
    clock = FakeClock()
    effective = resolver if resolver is not None else FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    if effective.clock is None:
        effective.clock = clock
    return drive_integration(root, resolver=effective, clock=clock)


@functools.lru_cache(maxsize=1)
def _contained_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-rel-contained-"))
    repo = _seed_repo(base)
    _write_commit(repo, "base.txt", "base\n", "scenario base")
    repo.git.checkout("-q", "-b", "feature")
    _write_commit(repo, "feature.txt", "feature\n", "feature change")
    repo.git.checkout("-q", "main")
    repo.git.merge("-q", "--no-ff", "-m", "landed by hand", "feature")
    repo.git.checkout("-q", "feature")
    repo.close()
    return base


@functools.lru_cache(maxsize=1)
def _feature_ahead_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-rel-ahead-"))
    repo = _seed_repo(base)
    _write_commit(repo, "base.txt", "base\n", "scenario base")
    repo.git.checkout("-q", "-b", "feature")
    _write_commit(repo, "feature.txt", "feature\n", "feature change")
    repo.close()
    return base


@functools.lru_cache(maxsize=1)
def _target_ahead_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-rel-target-ahead-"))
    repo = _seed_repo(base)
    _write_commit(repo, "base.txt", "base\n", "scenario base")
    repo.git.checkout("-q", "-b", "feature")
    _write_commit(repo, "feature.txt", "feature\n", "feature change")
    repo.git.checkout("-q", "main")
    _write_commit(repo, "target.txt", "target\n", "target change")
    repo.git.checkout("-q", "feature")
    repo.close()
    return base


@functools.lru_cache(maxsize=1)
def _diverged_clean_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-rel-diverged-clean-"))
    repo = _seed_repo(base)
    _write_commit(repo, "base.txt", "base\n", "scenario base")
    repo.git.checkout("-q", "-b", "feature")
    _write_commit(repo, "feature.txt", "feature\n", "feature change")
    repo.git.checkout("-q", "main")
    _write_commit(repo, "target.txt", "target\n", "target change")
    repo.git.checkout("-q", "feature")
    repo.close()
    return base


@functools.lru_cache(maxsize=1)
def _diverged_conflict_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-rel-diverged-conflict-"))
    repo = _seed_repo(base)
    _write_commit(repo, "base.txt", "base\n", "scenario base")
    repo.git.checkout("-q", "-b", "feature")
    _write_commit(repo, "shared.txt", "feature side\n", "feature change")
    repo.git.checkout("-q", "main")
    _write_commit(repo, "shared.txt", "target side\n", "target change")
    repo.git.checkout("-q", "feature")
    repo.close()
    return base


@functools.lru_cache(maxsize=1)
def _unrelated_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-rel-unrelated-"))
    repo = _seed_repo(base)
    _write_commit(repo, "base.txt", "base\n", "scenario base")
    repo.git.checkout("-q", "--orphan", "feature")
    repo.git.rm("-q", "-rf", ".")
    _write_commit(repo, "other.txt", "other\n", "unrelated root commit")
    repo.git.checkout("-q", "main")
    repo.git.checkout("-q", "feature")
    repo.close()
    return base


def test_feature_already_contained_in_the_target_changes_nothing(tmp_path: Path) -> None:
    root = _materialize(_contained_template(), tmp_path / "repo")
    target_before = _sha(root, "main")

    run = _drive(root)

    assert run.error is None, (
        f"the tick must not crash on an already-contained feature: {run.error}"
    )
    target_after = _sha(root, "main")
    assert target_after == target_before, (
        "a feature already contained in the target must leave the target untouched "
        f"(target moved from {target_before} to {target_after})"
    )


def test_feature_strictly_ahead_fast_forwards_the_target(tmp_path: Path) -> None:
    root = _materialize(_feature_ahead_template(), tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    target_before = _sha(root, "main")

    run = _drive(root)

    assert run.error is None, f"the tick must not crash on a strictly-ahead feature: {run.error}"
    assert _sha(root, "main") != target_before, (
        "a strictly-ahead feature must move the target forward"
    )
    assert_content_equivalence(
        root,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_target_strictly_ahead_carries_the_feature_work_forward(tmp_path: Path) -> None:
    root = _materialize(_target_ahead_template(), tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    target_before = _sha(root, "main")

    run = _drive(root)

    assert run.error is None, f"the tick must not crash on a strictly-ahead target: {run.error}"
    assert_content_equivalence(
        root,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )
    assert (root / "target.txt").is_file(), (
        "the target's own work must survive the integration of the feature"
    )


def test_diverged_without_conflicts_lands_the_feature(tmp_path: Path) -> None:
    root = _materialize(_diverged_clean_template(), tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    target_before = _sha(root, "main")

    run = _drive(root)

    assert run.error is None, f"the tick must not crash on a conflict-free divergence: {run.error}"
    assert_content_equivalence(
        root,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_diverged_with_conflicts_lands_the_resolved_feature(tmp_path: Path) -> None:
    root = _materialize(_diverged_conflict_template(), tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    target_before = _sha(root, "main")

    run = _drive(root, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))

    assert run.error is None, f"the tick must not crash on a conflicted divergence: {run.error}"
    assert run.resolver_launches >= 1, "a conflicted divergence must reach the resolver"
    assert_content_equivalence(
        root,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_unrelated_histories_land_the_feature_content(tmp_path: Path) -> None:
    root = _materialize(_unrelated_template(), tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    target_before = _sha(root, "main")

    run = _drive(root)

    assert run.error is None, f"the tick must not crash on unrelated histories: {run.error}"
    assert_content_equivalence(
        root,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )
