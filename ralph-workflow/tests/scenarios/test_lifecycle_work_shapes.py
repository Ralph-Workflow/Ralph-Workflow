from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from ralph.agents.timeout_clock import FakeClock
from tests._scenario_harness import AgentMode, FakeAgentRunner, commit_file, git
from tests.scenarios._content_equivalence import (
    FastImportCommit,
    assert_content_equivalence,
    drive_integration,
    fast_import_commits,
)

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(15),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "unrelated_conflicts_single_stop": (
        "four unrelated files still conflict in one stop via paired multi-file commits "
        "instead of four serial stop commits; content-equivalence on all four paths unchanged"
    ),
    "FakeClock_on_conflict_drives": (
        "conflicted work-shape integrate drives use FakeClock; landing asserts unchanged"
    ),
    "hot_conflict_fast_import": (
        "repeated hot-file commits are seeded with fast-import; every-commit conflict lands"
    ),
    "hot_conflict_two_repeats": (
        "two serial hot-file commits still repeat the same conflict on every commit; "
        "landing asserts unchanged versus the prior three-commit seed"
    ),
    "sibling_worktrees_three": (
        "three sibling worktrees still exercise concurrent feature landing; "
        "per-sibling content-equivalence unchanged versus the prior four-sibling seed"
    ),
    "many_commits_six": (
        "six serial feature commits still exceed the two-commit design point; "
        "content-equivalence unchanged versus the prior twelve-commit seed"
    ),
}

LANDED = "the feature's content must be present on the target branch"


def _seed_base(root: Path) -> str:
    return commit_file(root, "base.txt", "base\n", "scenario base")


def _few_commits(root: Path) -> str:
    _seed_base(root)
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "one.txt", "one\n", "feature one")
    commit_file(root, "two.txt", "two\n", "feature two")
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def _many_commits(root: Path, count: int) -> str:
    base = _seed_base(root)
    commits = [
        FastImportCommit(
            ref="refs/heads/feature",
            message=f"feature commit {index}",
            files=((f"file-{index}.txt", f"content {index}\n".encode()),),
            parent_sha=base if index == 0 else None,
            parent_index=index - 1 if index else None,
        )
        for index in range(count)
    ]
    fast_import_commits(root, commits)
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def _hot_conflict(root: Path) -> str:
    base = _seed_base(root)
    commits = [
        FastImportCommit(
            ref="refs/heads/feature",
            message=f"feature {label}",
            files=(("hot.txt", f"feature {label}\n".encode()),),
            parent_sha=base if index == 0 else None,
            parent_index=index - 1 if index else None,
        )
        for index, label in enumerate(("one", "two"))
    ]
    fast_import_commits(root, commits)
    git(root, "checkout", "-q", "main")
    commit_file(root, "hot.txt", "target side\n", "target change")
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def _unrelated_conflicts(root: Path) -> str:
    _seed_base(root)
    git(root, "checkout", "-q", "-b", "feature")
    for index in range(4):
        (root / f"file-{index}.txt").write_text(f"feature {index}\n", encoding="utf-8")
    git(root, "add", "--", *[f"file-{index}.txt" for index in range(4)])
    git(root, "commit", "-q", "-m", "feature unrelated files")
    git(root, "checkout", "-q", "main")
    for index in range(4):
        (root / f"file-{index}.txt").write_text(f"target {index}\n", encoding="utf-8")
    git(root, "add", "--", *[f"file-{index}.txt" for index in range(4)])
    git(root, "commit", "-q", "-m", "target unrelated files")
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def _range_with_merge_commit(root: Path) -> str:
    _seed_base(root)
    git(root, "checkout", "-q", "-b", "side")
    commit_file(root, "side.txt", "side\n", "side work")
    git(root, "checkout", "-q", "-b", "feature", "main")
    commit_file(root, "feature.txt", "feature\n", "feature work")
    git(root, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def _range_with_published_commit(root: Path) -> tuple[str, str]:
    _seed_base(root)
    published = commit_file(root, "published.txt", "published\n", "published commit")
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "work.txt", "work\n", "feature work")
    git(root, "checkout", "-q", "feature")
    return published, git(root, "rev-parse", "HEAD")


def _drive(root: Path, *, resolver: FakeAgentRunner | None = None) -> object:
    clock = FakeClock()
    effective = resolver if resolver is not None else FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    if getattr(effective, "clock", None) is None:
        effective.clock = clock
    return drive_integration(root, resolver=effective, clock=clock)


def test_few_commits_land(tmp_git_repo: Path) -> None:
    feature_sha = _few_commits(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = _drive(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on a two-commit feature: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_many_commits_land(tmp_git_repo: Path) -> None:
    feature_sha = _many_commits(tmp_git_repo, 6)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = _drive(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on a many-commit feature: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_one_hot_conflict_repeated_on_every_commit_still_lands(tmp_git_repo: Path) -> None:
    feature_sha = _hot_conflict(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = _drive(tmp_git_repo, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))

    assert run.error is None, f"the tick must not crash on a repeated hot conflict: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_unrelated_conflicts_across_many_files_land(tmp_git_repo: Path) -> None:
    feature_sha = _unrelated_conflicts(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = _drive(tmp_git_repo, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))

    assert run.error is None, f"the tick must not crash on unrelated conflicts: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_range_containing_merge_commits_still_lands(tmp_git_repo: Path) -> None:
    feature_sha = _range_with_merge_commit(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = _drive(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on a merge commit in the range: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_range_containing_published_commits_lands_without_rewriting_them(
    tmp_git_repo: Path,
) -> None:
    published_sha, feature_sha = _range_with_published_commit(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = _drive(tmp_git_repo)

    assert run.error is None, (
        f"the tick must not crash on a published commit in the range: {run.error}"
    )
    assert "main" in git(
        tmp_git_repo, "branch", "--contains", published_sha, "main", check=False
    ), "an already-published commit must stay reachable from the target, never rewritten"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_recorded_strategy_round_trips_and_a_fired_guard_switches_without_losing_resolved_work() -> (
    None
):
    from ralph.pipeline.auto_integrate_record import IntegrationRecord, parse_record_payload
    from ralph.pipeline.integration_strategy import (
        IntegrationRelation,
        IntegrationStrategy,
        IntegrationWorkShape,
        ObservedIntegration,
        apply_strategy_transition,
        record_strategy_decision,
    )

    class _LegacyIntegrationRecord(BaseModel):
        model_config = ConfigDict(frozen=True)

        record_version: int = 1
        phase: str
        target: str
        pre_feature_sha: str
        pre_target_sha: str | None

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        integrated_strategy="merge",
        integrated_strategy_reason="Shared history was discovered, so merge preserves it.",
    )
    payload = json.loads(record.model_dump_json())

    restored = parse_record_payload(payload)
    assert restored is not None
    assert restored.integrated_strategy == "merge"
    assert restored.integrated_strategy_reason == record.integrated_strategy_reason

    decision = record_strategy_decision(restored)
    assert decision.strategy is IntegrationStrategy.MERGE
    assert decision.reason == record.integrated_strategy_reason

    legacy = parse_record_payload(
        {
            "record_version": 1,
            "phase": "integrating",
            "target": "main",
            "pre_feature_sha": "a" * 40,
            "pre_target_sha": "b" * 40,
        }
    )
    assert legacy is not None
    assert legacy.integrated_strategy is None
    assert legacy.integrated_strategy_reason is None

    legacy_view = _LegacyIntegrationRecord.model_validate(payload)
    assert legacy_view.target == "main"

    transition = apply_strategy_transition(
        IntegrationStrategy.REBASE,
        observed=ObservedIntegration(
            relation=IntegrationRelation.DIVERGED,
            work_shape=IntegrationWorkShape(commit_count=3, history_already_published=True),
        ),
        attempts=1,
    )
    assert transition is not None
    assert transition.next_strategy is IntegrationStrategy.MERGE
    assert transition.preserves_resolved_work
    assert not transition.restarts_from_zero

    assert (
        apply_strategy_transition(
            IntegrationStrategy.REBASE,
            observed=ObservedIntegration(
                relation=IntegrationRelation.AHEAD,
                work_shape=IntegrationWorkShape(commit_count=2),
            ),
            attempts=1,
        )
        is None
    )


def test_many_sibling_worktrees_integrate(tmp_git_repo: Path) -> None:
    _seed_base(tmp_git_repo)
    git(tmp_git_repo, "checkout", "-q", "-b", "dummy")
    shas = []
    for index in range(3):
        sibling = tmp_git_repo.parent / f"sibling-{index}"
        git(tmp_git_repo, "worktree", "add", "-q", "-b", f"feature-{index}", str(sibling), "main")
        commit_file(sibling, f"sibling-{index}.txt", f"data {index}\n", f"commit {index}")
        shas.append(git(sibling, "rev-parse", "HEAD"))

    for index in range(3):
        sibling = tmp_git_repo.parent / f"sibling-{index}"
        run = _drive(sibling)
        assert run.error is None, f"sibling {index} must integrate: {run.error}"

    git(tmp_git_repo, "checkout", "-q", "main")
    for index, sha in enumerate(shas):
        assert_content_equivalence(
            tmp_git_repo,
            contract=f"sibling {index} work must land on main",
            target="main",
            feature=sha,
            source_refs=("main",),
        )
