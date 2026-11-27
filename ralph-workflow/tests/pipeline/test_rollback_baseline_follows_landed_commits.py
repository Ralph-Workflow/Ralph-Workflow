from __future__ import annotations

from pathlib import Path

import pytest
from git import Actor, Repo

from ralph.git.proposal_seal import rollback, seal_baseline, seal_pending
from ralph.pipeline.rollback_baseline import refresh_rollback_baseline_after_landing
from ralph.pipeline.state import PipelineState

pytestmark = pytest.mark.subprocess_e2e

_ACTOR = Actor("Ralph Test", "test@ralph.internal")


def _repo(root: Path) -> Repo:
    repo = Repo.init(root)
    repo.config_writer().set_value("user", "name", "Ralph Test").release()
    repo.config_writer().set_value("user", "email", "test@ralph.internal").release()
    (root / "policy.md").write_text("design_target: missing\n", encoding="utf-8")
    (root / "notes.txt").write_text("committed notes\n", encoding="utf-8")
    repo.index.add(["policy.md", "notes.txt"])
    repo.index.commit("initial", author=_ACTOR, committer=_ACTOR)
    return repo


def test_a_rejection_after_a_landed_commit_keeps_that_commit_and_the_pre_run_work(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (tmp_path / "notes.txt").write_text("operator work in progress\n", encoding="utf-8")
    run_start = seal_baseline(tmp_path, "run-start")
    assert run_start is not None
    state = PipelineState(phase="development", rollback_baseline_ref=run_start.ref)

    (tmp_path / "policy.md").write_text("design_target: /\n", encoding="utf-8")
    repo.index.add(["policy.md"])
    repo.index.commit("fix(policy): declare the design target", author=_ACTOR, committer=_ACTOR)
    state = refresh_rollback_baseline_after_landing(state, tmp_path)

    (tmp_path / "policy.md").write_text("design_target: rejected edit\n", encoding="utf-8")
    rejected = seal_pending(tmp_path, "run-start", 1)
    rollback(tmp_path, rejected.ref, baseline_ref=state.rollback_baseline_ref)

    assert (tmp_path / "policy.md").read_text(encoding="utf-8") == "design_target: /\n"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "operator work in progress\n"


def test_a_baseline_already_at_head_is_kept(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "notes.txt").write_text("operator work in progress\n", encoding="utf-8")
    run_start = seal_baseline(tmp_path, "run-start")
    assert run_start is not None
    state = PipelineState(phase="development", rollback_baseline_ref=run_start.ref)

    assert refresh_rollback_baseline_after_landing(state, tmp_path) is state
