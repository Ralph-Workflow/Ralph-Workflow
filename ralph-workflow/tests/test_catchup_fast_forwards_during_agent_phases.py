from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from ralph.config.models import UnifiedConfig
from ralph.git.git_command_queue import route_git_method
from ralph.pipeline.auto_integrate_catchup import (
    CATCHUP_DEFERRED_GIT_OPERATION,
    CATCHUP_DEFERRED_LOCAL_EDITS,
    CATCHUP_FAST_FORWARDED,
    AgentPhaseActivity,
    attempt_catchup_fast_forward,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(15)]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_uncommitted_edits_the_fast_forward_would_overwrite_are_never_touched": (
        "strengthened, not weakened: the check moved from 'outcome is not fast-forwarded' to "
        "the exact CATCHUP_DEFERRED_LOCAL_EDITS outcome, because an overlap with uncommitted "
        "edits is now a retry-next-tick deferral instead of a charged failure that quarantines; "
        "the head and the edit are still asserted untouched"
    ),
}

_ACTOR = Actor("Ralph Test", "test@ralph.internal")


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_remote_enabled": False,
                "auto_integrate_target": "main",
            }
        }
    )


def _commit(repo: Repo, message: str) -> str:
    route_git_method(repo, "add", ("-A",))
    route_git_method(repo, "commit", ("-q", "-m", message))
    return str(route_git_method(repo, "rev_parse", ("HEAD",))).strip()


def _feature_behind_main(tmp_path: Path) -> tuple[Path, Repo, str]:
    root = tmp_path / "project"
    root.mkdir()
    repo = Repo.init(root, initial_branch="main")
    with repo.config_writer() as config:
        config.set_value("user", "name", _ACTOR.name)
        config.set_value("user", "email", _ACTOR.email)
    (root / "app.txt").write_text("v1\n", encoding="utf-8")
    (root / "notes.txt").write_text("notes\n", encoding="utf-8")
    _commit(repo, "base")
    feature = tmp_path / "wt-feature"
    route_git_method(repo, "worktree", ("add", "-q", "-b", "feature", str(feature)))
    (root / "app.txt").write_text("v2\n", encoding="utf-8")
    newest = _commit(repo, "main moves ahead")
    return feature, Repo(feature), newest


def _head(repo: Repo) -> str:
    return str(route_git_method(repo, "rev_parse", ("HEAD",))).strip()


def test_a_behind_checkout_fast_forwards_while_an_agent_phase_is_active(
    tmp_path: Path,
) -> None:
    feature, feature_repo, newest = _feature_behind_main(tmp_path)
    phases = AgentPhaseActivity()

    with phases.agent_phase(feature):
        outcome = attempt_catchup_fast_forward(_config(), feature, agent_phases=phases)

    assert outcome == CATCHUP_FAST_FORWARDED
    assert _head(feature_repo) == newest
    assert (feature / "app.txt").read_text(encoding="utf-8") == "v2\n"


def test_uncommitted_agent_edits_on_other_files_do_not_block_the_fast_forward(
    tmp_path: Path,
) -> None:
    feature, feature_repo, newest = _feature_behind_main(tmp_path)
    (feature / "notes.txt").write_text("agent is editing this\n", encoding="utf-8")
    phases = AgentPhaseActivity()

    with phases.agent_phase(feature):
        outcome = attempt_catchup_fast_forward(_config(), feature, agent_phases=phases)

    assert outcome == CATCHUP_FAST_FORWARDED
    assert _head(feature_repo) == newest
    assert (feature / "notes.txt").read_text(encoding="utf-8") == "agent is editing this\n"


def test_uncommitted_edits_the_fast_forward_would_overwrite_are_never_touched(
    tmp_path: Path,
) -> None:
    feature, feature_repo, _newest = _feature_behind_main(tmp_path)
    before = _head(feature_repo)
    (feature / "app.txt").write_text("agent edit in progress\n", encoding="utf-8")

    outcome = attempt_catchup_fast_forward(_config(), feature, agent_phases=AgentPhaseActivity())

    assert outcome == CATCHUP_DEFERRED_LOCAL_EDITS
    assert _head(feature_repo) == before
    assert (feature / "app.txt").read_text(encoding="utf-8") == "agent edit in progress\n"


def test_an_overlap_is_retried_every_tick_and_never_quarantined(tmp_path: Path) -> None:
    feature, feature_repo, newest = _feature_behind_main(tmp_path)
    (feature / "app.txt").write_text("agent edit in progress\n", encoding="utf-8")
    phases = AgentPhaseActivity()

    outcomes = [
        attempt_catchup_fast_forward(_config(), feature, agent_phases=phases) for _ in range(8)
    ]
    route_git_method(feature_repo, "checkout", ("--", "app.txt"))
    landed = attempt_catchup_fast_forward(_config(), feature, agent_phases=phases)

    assert set(outcomes) == {CATCHUP_DEFERRED_LOCAL_EDITS}
    assert landed == CATCHUP_FAST_FORWARDED
    assert _head(feature_repo) == newest


def test_an_untracked_file_the_incoming_commit_adds_defers_instead_of_failing(
    tmp_path: Path,
) -> None:
    feature, feature_repo, _newest = _feature_behind_main(tmp_path)
    root = feature.parent / "project"
    (root / "added.txt").write_text("from main\n", encoding="utf-8")
    newer = _commit(Repo(root), "main adds a file")
    (feature / "added.txt").write_text("agent's own untracked file\n", encoding="utf-8")

    outcome = attempt_catchup_fast_forward(_config(), feature, agent_phases=AgentPhaseActivity())

    assert outcome == CATCHUP_DEFERRED_LOCAL_EDITS
    assert _head(feature_repo) != newer
    assert (feature / "added.txt").read_text(encoding="utf-8") == "agent's own untracked file\n"


def test_a_git_operation_in_progress_is_the_one_thing_that_defers_it(tmp_path: Path) -> None:
    feature, feature_repo, _newest = _feature_behind_main(tmp_path)
    before = _head(feature_repo)
    git_dir = feature / ".git"
    worktree_git_dir = (
        git_dir.read_text(encoding="utf-8").split(":", 1)[1].strip()
        if git_dir.is_file()
        else str(git_dir)
    )
    from pathlib import Path as _Path

    (_Path(worktree_git_dir) / "MERGE_HEAD").write_text(before + "\n", encoding="utf-8")

    outcome = attempt_catchup_fast_forward(_config(), feature, agent_phases=AgentPhaseActivity())

    assert outcome == CATCHUP_DEFERRED_GIT_OPERATION
    assert _head(feature_repo) == before


def test_a_dirty_copy_already_matching_the_incoming_version_is_absorbed(tmp_path: Path) -> None:
    feature, feature_repo, newest = _feature_behind_main(tmp_path)
    (feature / "app.txt").write_text("v2\n", encoding="utf-8")

    outcome = attempt_catchup_fast_forward(_config(), feature, agent_phases=AgentPhaseActivity())

    assert outcome == CATCHUP_FAST_FORWARDED
    assert _head(feature_repo) == newest
    assert (feature / "app.txt").read_text(encoding="utf-8") == "v2\n"
    status = str(route_git_method(feature_repo, "status", ("--porcelain", "--", "app.txt")))
    assert status.strip() == ""


def test_one_genuinely_different_overlap_still_defers_and_touches_nothing(
    tmp_path: Path,
) -> None:
    feature, feature_repo, _newest = _feature_behind_main(tmp_path)
    root = feature.parent / "project"
    (root / "notes.txt").write_text("main notes\n", encoding="utf-8")
    _commit(Repo(root), "main edits notes too")
    before = _head(feature_repo)
    (feature / "app.txt").write_text("v2\n", encoding="utf-8")
    (feature / "notes.txt").write_text("agent's own notes\n", encoding="utf-8")

    outcome = attempt_catchup_fast_forward(_config(), feature, agent_phases=AgentPhaseActivity())

    assert outcome == CATCHUP_DEFERRED_LOCAL_EDITS
    assert _head(feature_repo) == before
    assert (feature / "app.txt").read_text(encoding="utf-8") == "v2\n"
    assert (feature / "notes.txt").read_text(encoding="utf-8") == "agent's own notes\n"


def test_an_untracked_copy_of_an_incoming_new_file_is_absorbed(tmp_path: Path) -> None:
    feature, feature_repo, _newest = _feature_behind_main(tmp_path)
    root = feature.parent / "project"
    (root / "added.txt").write_text("from main\n", encoding="utf-8")
    newer = _commit(Repo(root), "main adds a file")
    (feature / "added.txt").write_text("from main\n", encoding="utf-8")

    outcome = attempt_catchup_fast_forward(_config(), feature, agent_phases=AgentPhaseActivity())

    assert outcome == CATCHUP_FAST_FORWARDED
    assert _head(feature_repo) == newer
    assert (feature / "added.txt").read_text(encoding="utf-8") == "from main\n"


def test_a_staged_version_different_from_both_sides_is_never_absorbed(tmp_path: Path) -> None:
    feature, feature_repo, _newest = _feature_behind_main(tmp_path)
    before = _head(feature_repo)
    (feature / "app.txt").write_text("staged agent work\n", encoding="utf-8")
    route_git_method(feature_repo, "add", ("app.txt",))
    (feature / "app.txt").write_text("v2\n", encoding="utf-8")

    outcome = attempt_catchup_fast_forward(_config(), feature, agent_phases=AgentPhaseActivity())

    assert outcome == CATCHUP_DEFERRED_LOCAL_EDITS
    assert _head(feature_repo) == before
    staged = str(route_git_method(feature_repo, "show", (":app.txt",)))
    assert staged.strip() == "staged agent work"
