from __future__ import annotations

import functools
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from git import Repo

from ralph.agents.timeout_clock import FakeClock
from tests._scenario_harness import (
    AgentMode,
    FakeAgentRunner,
    git,
    paused_text_rebase,
)
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "paused_or_diverged_template_reuse": (
        "continue materializes a cached paused rebase; other operator actions materialize a "
        "cached diverged topology without starting a rebase first"
    ),
    "FakeClock_on_integrate_drives": (
        "operator-interleave integrate drives use FakeClock; observation asserts unchanged"
    ),
    "GitPython_feature_sha_reads": (
        "feature tip reads use GitPython instead of git rev-parse subprocess"
    ),
    "operator_preland_merge_covers_cherry_pick": (
        "MERGE: keep continue/reset/manual-land; cherry-pick's next-tick observation of "
        "operator-prelanded feature content is covered by manual-land's content-equivalence"
    ),
    "operator_abort_covered_by_diverged_conflict": (
        "MERGE: keep continue/reset/manual-land; abort-then-reintegrate of a conflicted "
        "divergence is covered by test_diverged_with_conflicts_lands_the_resolved_feature"
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


@functools.lru_cache(maxsize=1)
def _paused_rebase_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-interleave-paused-"))
    repo = _seed_repo(base)
    repo.close()
    paused_text_rebase(base)
    return base


@functools.lru_cache(maxsize=1)
def _diverged_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-interleave-diverged-"))
    repo = _seed_repo(base)
    _write_commit(repo, "shared.txt", "base\n", "scenario base")
    repo.git.checkout("-q", "-b", "feature")
    _write_commit(repo, "shared.txt", "feature\n", "feature change")
    repo.git.checkout("-q", "main")
    _write_commit(repo, "shared.txt", "target\n", "target change")
    repo.git.checkout("-q", "feature")
    repo.close()
    return base


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


def _drive(root: Path) -> object:
    clock = FakeClock()
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES, clock=clock)
    return drive_integration(root, resolver=runner, clock=clock)


@pytest.mark.parametrize("action", ["continue", "reset", "manual-land"])
def test_operator_action_between_ticks_is_observed(tmp_path: Path, action: str) -> None:
    if action == "continue":
        root = _materialize(_paused_rebase_template(), tmp_path / "repo")
    else:
        root = _materialize(_diverged_template(), tmp_path / "repo")
    feature_sha = _sha(root, "feature")
    if action == "continue":
        (root / "shared.txt").write_text("operator resolution\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "-c", "core.editor=:", "rebase", "--continue")
    elif action == "reset":
        git(root, "reset", "--hard", "main")
    else:
        git(root, "checkout", "-q", "main")
        git(root, "merge", "-q", "--no-ff", "-m", "manual landing", "feature", check=False)
        (root / "shared.txt").write_text("operator resolution\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "-c", "core.editor=:", "commit", "-m", "manual landing")
        git(root, "checkout", "-q", "feature")

    run = _drive(root)

    assert run.error is None, f"the next tick must observe an operator {action}: {run.error}"
    if action != "reset":
        assert_content_equivalence(
            root,
            contract=f"operator {action} work must reach the target",
            feature=feature_sha,
            source_refs=("main",),
        )
