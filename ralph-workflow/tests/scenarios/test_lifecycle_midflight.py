from __future__ import annotations

import functools
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from git import Repo

from ralph.agents.timeout_clock import FakeClock
from tests._scenario_harness import AgentMode, FakeAgentRunner, git, paused_text_rebase
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "paused_rebase_template_reuse": (
        "paused conflict topology is built once and hardlink-copytree'd per case"
    ),
    "FakeClock_on_integrate_drives": (
        "mid-flight integrate drives use FakeClock; landing and retention asserts unchanged"
    ),
    "GitPython_sha_and_contains_reads": (
        "feature/target tip and branch-contains checks use GitPython instead of git subprocess"
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


@functools.lru_cache(maxsize=1)
def _paused_rebase_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-midflight-paused-"))
    readme = base / "README.md"
    readme.write_text("seed\n", encoding="utf-8")
    repo = Repo.init(base, initial_branch="main")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("commit", "gpgsign", "false")
    repo.index.add(["README.md"])
    repo.index.commit("seed")
    repo.close()
    paused_text_rebase(base)
    return base


def _materialize_paused_rebase(dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _paused_rebase_template(),
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


def _contains(root: Path, commit: str, branch: str = "main") -> bool:
    repo = Repo(root)
    try:
        return repo.is_ancestor(commit, branch)
    finally:
        repo.close()


def _drive(root: Path, *, resolver: object | None = None) -> object:
    clock = FakeClock()
    if resolver is None:
        resolver = FakeAgentRunner(AgentMode.EDITS_AND_STAGES, clock=clock)
    elif isinstance(resolver, FakeAgentRunner) and resolver.clock is None:
        resolver.clock = clock
    return drive_integration(root, resolver=resolver, clock=clock)


def _resolve_current_stop(root: Path) -> None:
    (root / "shared.txt").write_text("first stop resolved\n", encoding="utf-8")
    git(root, "add", "shared.txt")
    git(root, "-c", "core.editor=:", "rebase", "--continue", check=False)


def test_strategy_switch_keeps_partly_resolved_work(tmp_path: Path) -> None:
    root = _materialize_paused_rebase(tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    _resolve_current_stop(root)
    resolved = (root / "shared.txt").read_text(encoding="utf-8")

    run = _drive(root, resolver=lambda _root, _target, _stop: False)

    assert run.error is None, f"a strategy switch must not crash: {run.error}"
    assert (root / "shared.txt").read_text(encoding="utf-8") == resolved, (
        "switching strategy mid-flight must keep already-resolved work"
    )
    assert_content_equivalence(
        root,
        contract="a mid-flight strategy switch must still land the feature",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_new_feature_commits_during_rebase_are_absorbed(tmp_path: Path) -> None:
    root = _materialize_paused_rebase(tmp_path / "repo")
    old_tip = _sha(root, "feature")
    repo = Repo(root)
    try:
        tree = repo.commit(old_tip).tree.hexsha
        new_tip = repo.git.commit_tree(tree, "-p", old_tip, "-m", "late feature work")
        repo.git.update_ref("refs/heads/feature", new_tip, old_tip)
    finally:
        repo.close()

    run = _drive(root, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))

    assert run.error is None, f"new feature work must be absorbed without a crash: {run.error}"
    assert _contains(root, new_tip), (
        "commits added to the feature during integration must reach the target"
    )


def test_new_target_commits_during_rebase_are_absorbed(tmp_path: Path) -> None:
    root = _materialize_paused_rebase(tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    old_target = _sha(root, "main")
    repo = Repo(root)
    try:
        tree = repo.commit(old_target).tree.hexsha
        new_target = repo.git.commit_tree(tree, "-p", old_target, "-m", "late target work")
        repo.git.update_ref("refs/heads/main", new_target, old_target)
    finally:
        repo.close()

    run = _drive(root, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))

    assert run.error is None, f"new target work must be absorbed without a crash: {run.error}"
    assert _contains(root, new_target), (
        "a target commit added mid-flight must remain reachable after landing"
    )
    assert_content_equivalence(
        root,
        contract="the feature must land after the target moves mid-flight",
        feature=feature_sha,
        source_refs=(new_target,),
    )
