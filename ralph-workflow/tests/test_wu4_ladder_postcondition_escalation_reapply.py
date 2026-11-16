from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ralph.pipeline.integration_decision import (
    AttemptState,
    CleanupLadderRung,
    DecisionContext,
    EscalationRung,
    NextAction,
    decide,
)
from ralph.pipeline.integration_ladder import run_ladder_rung
from ralph.pipeline.integration_observation import (
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
)
from ralph.pipeline.integration_preservation_bundle import (
    preserve_integration_state,
    reapply_preserved_progress,
)
from tests._fake_git_clock import FakeClock
from tests._integration_ladder_fakes import _FakePlumbing, _FakeQuarantineFs, _ledger

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.required_auto_integrate_e2e]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_identical_pair_past_repeat_threshold_selects_different_rung": (
        "the gated repeat-threshold contract charges an identical observation/action pair "
        "exactly repeat_threshold times before climbing, so escalation is pinned at the "
        "threshold instead of on the second identical pair"
    ),
    "test_identical_pair_past_repeat_threshold_escalates_on_ladder_rung": (
        "the gated repeat-threshold contract charges an identical observation/action pair "
        "exactly repeat_threshold times before climbing, so escalation is pinned at the "
        "threshold instead of on the second identical pair"
    ),
}


def _conflict_observation() -> Observation:
    return Observation(
        target_vs_feature=TargetVsFeature.EQUAL,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.UNMERGED,
        head=HeadState.DETACHED,
        in_progress_kind=InProgressKind.REBASE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )


def test_unchanged_rung_records_failed_post_condition_without_explicit_verifier(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    observation = "equal|unmerged|rebase|consistent|detached"

    result = run_ladder_rung(
        CleanupLadderRung.QUARANTINE,
        root=tmp_path,
        ledger=ledger,
        git=_FakePlumbing(),
        fs=_FakeQuarantineFs(),
        clock=FakeClock(),
        paths=("blocked.txt",),
        observe=lambda: observation,
    )

    assert result.outcome == "failed"
    assert result.reason.startswith("failed:post-condition:")
    assert not result.post_condition_verified
    evidence = ledger.read().ladder_evidence
    assert evidence.get("failed:post-condition:quarantine") == result.reason
    assert evidence.get("failed:post-condition:quarantine_and_restore") == result.reason


def test_identical_pair_past_repeat_threshold_selects_different_rung() -> None:
    obs = _conflict_observation()
    attempts = AttemptState()
    first = decide(DecisionContext(observation=obs, attempts=attempts))
    assert first is NextAction.DISPATCH_RESOLVER

    after_first = attempts.with_attempt(obs, first, is_unknown=False)
    second = decide(DecisionContext(observation=obs, attempts=after_first))
    assert second is first

    after_second = after_first.with_attempt(obs, second, is_unknown=False)
    third = decide(DecisionContext(observation=obs, attempts=after_second))

    assert third is not first
    assert third is not NextAction.DISPATCH_RESOLVER


def test_identical_pair_past_repeat_threshold_escalates_on_ladder_rung() -> None:
    obs = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.DETACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    attempts = AttemptState(rung=EscalationRung.REPAIR)
    first = decide(DecisionContext(observation=obs, attempts=attempts))
    assert first is NextAction.REPAIR

    after_first = attempts.with_attempt(obs, first, is_unknown=False)
    second = decide(DecisionContext(observation=obs, attempts=after_first))
    assert second is first

    after_second = after_first.with_attempt(obs, second, is_unknown=False)
    third = decide(DecisionContext(observation=obs, attempts=after_second))

    assert third is not NextAction.REPAIR
    assert after_second.rung is not EscalationRung.REPAIR


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Ralph Test",
        "GIT_AUTHOR_EMAIL": "ralph@example.com",
        "GIT_COMMITTER_NAME": "Ralph Test",
        "GIT_COMMITTER_EMAIL": "ralph@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(5)
def test_destructive_rung_reapplies_captured_index_and_hunk_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    target = repo / "conflict.txt"
    target.write_text("base\n", encoding="utf-8")
    _git(repo, "add", "conflict.txt")
    _git(repo, "commit", "-q", "-m", "base")

    _git(repo, "checkout", "-q", "-b", "feature")
    target.write_text("feature-side\n", encoding="utf-8")
    _git(repo, "add", "conflict.txt")
    _git(repo, "commit", "-q", "-m", "feature")

    _git(repo, "checkout", "-q", "main")
    target.write_text("main-side\n", encoding="utf-8")
    _git(repo, "add", "conflict.txt")
    _git(repo, "commit", "-q", "-m", "main")

    merge = subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", "feature"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    )
    assert merge.returncode != 0

    resolved = "resolved-both-sides\n"
    target.write_text(resolved, encoding="utf-8")
    _git(repo, "add", "conflict.txt")

    staged_before = _git(repo, "diff", "--cached", "--", "conflict.txt")
    assert "resolved-both-sides" in staged_before

    state_dir = repo / ".agent"
    state_dir.mkdir()
    bundle = preserve_integration_state(repo, state_dir=state_dir, target="main")
    assert bundle is not None
    assert bundle.index_copy is not None

    _git(repo, "reset", "--hard", "HEAD")
    assert "resolved-both-sides" not in (repo / "conflict.txt").read_text(encoding="utf-8")
    staged_after_reset = _git(repo, "diff", "--cached", "--", "conflict.txt")
    assert staged_after_reset == ""

    assert reapply_preserved_progress(repo, bundle) is True
    assert (repo / "conflict.txt").read_text(encoding="utf-8") == resolved
    staged_after = _git(repo, "diff", "--cached", "--", "conflict.txt")
    assert "resolved-both-sides" in staged_after


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(5)
def test_quarantine_rung_reapplies_resolved_index_after_restore(tmp_path: Path) -> None:
    from ralph.pipeline.integration_ladder import default_file_quarantine, default_git_plumbing

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    tracked = repo / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "base")

    tracked.write_text("resolved-hunk\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    dirty = repo / "blocking.txt"
    dirty.write_text("block\n", encoding="utf-8")

    staged_before = _git(repo, "diff", "--cached", "--", "tracked.txt")
    assert "resolved-hunk" in staged_before

    ledger = _ledger(repo)
    result = run_ladder_rung(
        CleanupLadderRung.QUARANTINE,
        root=repo,
        ledger=ledger,
        git=default_git_plumbing(),
        fs=default_file_quarantine(),
        clock=FakeClock(),
        state_dir=repo / ".agent",
        paths=("blocking.txt",),
        observe=lambda: "changed-after-quarantine",
    )

    assert result.outcome in {"succeeded", "failed"}
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "resolved-hunk\n"
    staged_after = _git(repo, "diff", "--cached", "--", "tracked.txt")
    assert "resolved-hunk" in staged_after
    assert not dirty.exists() or "quarantine" in str(
        ledger.read().ladder_evidence.get("preserved:quarantine", "")
    )
