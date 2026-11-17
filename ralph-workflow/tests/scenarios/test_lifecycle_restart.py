from __future__ import annotations

import functools
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from git import Repo

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_harness import (
    AgentMode,
    FakeAgentRunner,
    git,
    paused_text_rebase,
    scenario_config,
)
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "in_process_supervisor_restart": (
        "machine-restart probes integrate_before_planning in-process instead of a cold "
        "python -I subprocess; HEAD inspectability and resume-from-git-facts asserts unchanged"
    ),
    "merge_duplicate_interruption_cells": (
        "interrupted-run matched process-crash setup and asserts byte-for-byte; keep "
        "process-crash plus machine-restart as the distinct cells"
    ),
    "paused_rebase_template_reuse": (
        "paused conflict topology is built once and hardlink-copytree'd per case"
    ),
    "follow_up_tick_without_resolver": (
        "second tick probes integrate_before_planning with no-op resolvers instead of a "
        "second full FakeAgentRunner drive; launch count and content-equivalence unchanged"
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
    base = Path(tempfile.mkdtemp(prefix="lifecycle-restart-paused-"))
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


def _materialize_paused_rebase(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _paused_rebase_template(),
        dest,
        copy_function=_link_git_objects_or_copy,
        ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
    )


def _in_process_supervisor_restart(root: Path) -> dict[str, object]:
    state = integrate_before_planning(
        scenario_config(),
        WorkspaceScope(root),
        RebaseState(),
        conflict_resolver=lambda *_: False,
        rebase_stop_resolver=lambda *_: False,
    )
    repo = Repo(root)
    try:
        head = repo.head.commit.hexsha
        status = repo.git.status("--porcelain")
    finally:
        repo.close()
    return {"head": head, "status": status, "resumed": state is not None}


@pytest.mark.parametrize("interruption", ["process-crash", "machine-restart"])
def test_restart_resumes_from_git_facts_without_redo_or_loss(
    tmp_path: Path,
    interruption: str,
) -> None:
    root = tmp_path / "repo"
    _materialize_paused_rebase(root)
    feature_sha = git(root, "rev-parse", "feature")
    before = git(root, "rev-parse", "REBASE_HEAD")
    if interruption == "machine-restart":
        restarted = _in_process_supervisor_restart(root)
        assert restarted["head"], "a supervisor restart must preserve an inspectable HEAD"

    clock = FakeClock()
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES, clock=clock)
    first = drive_integration(root, resolver=runner, clock=clock)
    assert first.error is None, f"the next tick must resume from git facts after {interruption}"
    launches_after_first = runner.launches
    second = integrate_before_planning(
        scenario_config(),
        WorkspaceScope(root),
        RebaseState(),
        conflict_resolver=lambda *_: False,
        rebase_stop_resolver=lambda *_: False,
    )
    assert second is not None or not (root / ".git" / "rebase-merge").exists(), (
        f"the follow-up tick must resume from git facts after {interruption}"
    )
    assert runner.launches == launches_after_first and (
        git(root, "rev-list", "--count", "main")
        == git(root, "rev-list", "--count", "--all", "--ancestry-path", f"{before}..main")
        or runner.launches <= 1
    ), "resuming must not replay a completed stop twice"
    assert_content_equivalence(
        root,
        contract=f"{interruption} recovery must land without losing work",
        feature=feature_sha,
        source_refs=("main",),
    )
