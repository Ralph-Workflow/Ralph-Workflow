from __future__ import annotations

import subprocess
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from git import Repo
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.git import remote_push
from ralph.git.merge import branch_sha
from ralph.phases.required_artifacts import retry_hint_path
from ralph.pipeline import run_loop
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
    auto_integrate_on_phase_transition,
)
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_remote_push import push_target_after_landing
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.publication_gate import (
    last_publication_gate_result,
    publication_gate_feedback,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.subprocess_e2e

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_a_red_gate_keeps_the_run_commits_off_the_target": (
        "user requirement: pushing a run's commits to origin/main is a law and the publication "
        "gate never blocks a landing; replaced by test_a_red_gate_lands_and_routes_its_feedback"
    ),
    "test_a_fix_on_a_new_tree_reruns_the_gate_and_publishes": (
        "user requirement: the gate is advisory, so the red tree already lands; a new tree "
        "re-running the gate is pinned by test_a_red_gate_lands_and_routes_its_feedback"
    ),
    "test_an_unchanged_blocked_tree_is_evaluated_and_landed_at_most_once": (
        "user requirement: the gate never holds commits; replaced by "
        "test_a_red_tree_is_gated_once_and_lands_at_planning_entry"
    ),
    "test_planning_proceeds_past_a_gate_blocked_tree_instead_of_waiting": (
        "user requirement: no tree is gate-blocked any more; planning entry lands the commits, "
        "pinned by test_a_red_tree_is_gated_once_and_lands_at_planning_entry"
    ),
    "test_a_planning_entry_gate_refusal_tells_the_next_development_pass_why": (
        "user requirement: the gate never refuses a landing; red-gate feedback routing is "
        "pinned by test_a_red_gate_lands_and_routes_its_feedback"
    ),
    "test_a_retained_record_for_a_gate_refused_tree_is_released_instead_of_looping": (
        "user requirement: the gate never refuses a landing, so no record is retained or "
        "released for a gate-refused tree"
    ),
    "test_an_owned_reintegration_refused_by_a_fresh_gate_run_is_released": (
        "user requirement: the gate never refuses a landing, so no owned reintegration is "
        "released for a gate refusal"
    ),
    "test_an_owned_reintegration_already_refused_by_the_gate_is_released": (
        "user requirement: the gate never refuses a landing, so no owned reintegration is "
        "released for a gate refusal"
    ),
}

_GATE_FACT = "RALPH-FACT: authoritative_verify_command: `test -f gate-ok`\n"
_POLICY = Path("docs") / "ralph-workflow-policy" / "verification-policy.md"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True, timeout=30
    )


def _commit(root: Path, relative: str, content: str, message: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo = Repo(root)
    try:
        repo.index.add([relative])
        return repo.index.commit(message).hexsha
    finally:
        repo.close()


def _target_of(repo: Path) -> str:
    return _run(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip().removeprefix("refs/heads/")


def _with_origin(repo: Path, target: str) -> Path:
    bare = repo.parent / "origin.git"
    assert _run(repo, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", "origin", str(bare)).returncode == 0
    assert _run(repo, "fetch", "origin").returncode == 0
    return bare


def _feature(
    tmp_git_repo: Path, tmp_path: Path, *, fact: str | None, remote: bool = False
) -> tuple[str, Path, Path | None]:
    target = _target_of(tmp_git_repo)
    if fact is not None:
        _commit(tmp_git_repo, str(_POLICY), f"# Verification\n\n{fact}", "declare gate")
    bare = _with_origin(tmp_git_repo, target) if remote else None
    feature = tmp_path / "feature"
    assert _run(tmp_git_repo, "worktree", "add", "-b", "feature", str(feature)).returncode == 0
    return target, feature, bare


def _config(target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )


def _integrate(feature: Path, target: str) -> RebaseState:
    outcome = auto_integrate_after_commit(
        _config(target),
        WorkspaceScope(feature),
        RebaseState(),
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )
    assert outcome is not None
    return outcome


def _counting_red_gate(tmp_path: Path) -> tuple[str, Path]:
    runs = tmp_path / "gate-runs.log"
    fact = f"RALPH-FACT: authoritative_verify_command: `echo run >> {runs}; exit 2`\n"
    return fact, runs


def _logs(level: str) -> tuple[list[str], int]:
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level=level, format="{message}")
    return lines, sink


@pytest.mark.timeout_seconds(30)
def test_a_red_gate_lands_and_routes_its_feedback(tmp_git_repo: Path, tmp_path: Path) -> None:
    target, feature, bare = _feature(tmp_git_repo, tmp_path, fact=_GATE_FACT, remote=True)
    assert bare is not None
    feature_tip = _commit(feature, "work.txt", "work\n", "run commit without gate-ok")
    critical, sink = _logs("CRITICAL")
    try:
        outcome = _integrate(feature, target)
    finally:
        logger.remove(sink)

    assert outcome.fast_forwarded is True
    assert branch_sha(tmp_git_repo, target) == feature_tip
    assert _run(bare, "rev-parse", target).stdout.strip() == feature_tip
    assert not [line for line in critical if "did NOT land" in line]
    gate = last_publication_gate_result(feature)
    assert gate is not None
    assert gate.passed is False
    bundle = cached_default_policy_bundle()
    assert publication_gate_feedback(feature, bundle.pipeline, "development_commit") is not None
    hint = feature / retry_hint_path("development", pipeline_policy=bundle.pipeline)
    assert "test -f gate-ok" in hint.read_text(encoding="utf-8")


@pytest.mark.timeout_seconds(30)
def test_a_green_gate_publishes_the_run_commits(tmp_git_repo: Path, tmp_path: Path) -> None:
    target, feature, _bare = _feature(tmp_git_repo, tmp_path, fact=_GATE_FACT)
    feature_tip = _commit(feature, "gate-ok", "ok\n", "run commit that passes the gate")

    outcome = _integrate(feature, target)

    assert outcome.fast_forwarded is True
    assert branch_sha(tmp_git_repo, target) == feature_tip


@pytest.mark.timeout_seconds(30)
def test_a_project_without_a_declared_gate_publishes_as_before(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    target, feature, _bare = _feature(tmp_git_repo, tmp_path, fact=None)
    feature_tip = _commit(feature, "work.txt", "work\n", "run commit")

    outcome = _integrate(feature, target)

    assert outcome.fast_forwarded is True
    assert branch_sha(tmp_git_repo, target) == feature_tip


@pytest.mark.timeout_seconds(60)
def test_a_red_tree_is_gated_once_and_lands_at_planning_entry(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    fact, runs = _counting_red_gate(tmp_path)
    target, feature, _bare = _feature(tmp_git_repo, tmp_path, fact=fact)
    feature_tip = _commit(feature, "work.txt", "work\n", "red run commit")
    config = _config(target)

    outcomes = [
        integrate_before_planning(config, WorkspaceScope(feature), RebaseState())
        for _tick in range(5)
    ]
    ctx = SimpleNamespace(
        config=config,
        workspace_scope=WorkspaceScope(feature),
        policy_bundle=cached_default_policy_bundle(),
        active_display=None,
    )
    _state, held = run_loop.ensure_planning_in_sync(
        PipelineState(phase="planning", rebase=outcomes[-1] or RebaseState()), ctx, "planning"
    )

    assert held is None
    assert runs.read_text(encoding="utf-8").splitlines() == ["run"]
    assert branch_sha(tmp_git_repo, target) == feature_tip


@pytest.mark.timeout_seconds(60)
def test_committed_work_lands_while_the_worktree_has_uncommitted_edits(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    fact, runs = _counting_red_gate(tmp_path)
    target, feature, bare = _feature(tmp_git_repo, tmp_path, fact=fact, remote=True)
    assert bare is not None
    _commit(feature, "one.txt", "one\n", "first run commit")
    feature_tip = _commit(feature, "two.txt", "two\n", "second run commit")
    (feature / "two.txt").write_text("agent mid-edit\n", encoding="utf-8")
    config = _config(target)

    planning = integrate_before_planning(config, WorkspaceScope(feature), RebaseState())
    boundary = auto_integrate_on_phase_transition(config, WorkspaceScope(feature), RebaseState())

    assert planning is not None
    assert planning.fast_forwarded is True
    assert boundary is None or "worktree not clean" not in (boundary.last_reason or "")
    assert branch_sha(tmp_git_repo, target) == feature_tip
    assert _run(bare, "rev-parse", target).stdout.strip() == feature_tip
    assert _run(feature, "rev-parse", "HEAD").stdout.strip() == feature_tip
    assert (feature / "two.txt").read_text(encoding="utf-8") == "agent mid-edit\n"
    assert _run(feature, "status", "--porcelain").stdout.strip() == "M two.txt"
    assert not runs.exists()


@pytest.mark.timeout_seconds(60)
def test_a_dirty_worktree_behind_its_target_waits_and_says_so_once(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    target, feature, _bare = _feature(tmp_git_repo, tmp_path, fact=None)
    feature_tip = _commit(feature, "work.txt", "work\n", "run commit")
    target_tip = _commit(tmp_git_repo, "other.txt", "other\n", "target moved")
    (feature / "work.txt").write_text("agent mid-edit\n", encoding="utf-8")
    config = _config(target)
    infos, sink = _logs("INFO")
    try:
        outcomes = [
            integrate_before_planning(config, WorkspaceScope(feature), RebaseState())
            for _tick in range(3)
        ]
    finally:
        logger.remove(sink)

    assert all(outcome is not None and outcome.last_action == "skipped" for outcome in outcomes)
    waits = [line.strip() for line in infos if "wait for a clean worktree" in line]
    assert waits == [f"1 committed commits wait for a clean worktree to rebase onto {target}"]
    assert branch_sha(tmp_git_repo, target) == target_tip
    assert _run(feature, "rev-parse", "HEAD").stdout.strip() == feature_tip
    assert (feature / "work.txt").read_text(encoding="utf-8") == "agent mid-edit\n"


class _RecordingClock:
    def __init__(self) -> None:
        self.current = 1000.0
        self.slept: deque[float] = deque(maxlen=16)

    def now(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.current += seconds


class _FlakyNetworkClient:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.pushes = 0

    def refresh_target(
        self, repo_root: Path, target: str, *, timeout_seconds: float, remote: str
    ) -> str:
        raise AssertionError("a push after landing never refreshes the target")

    def observe_remote_sha(self, repo_root: Path, target: str, *, remote: str) -> str | None:
        return _run(repo_root, "rev-parse", f"{remote}/{target}").stdout.strip() or None

    def push_branch(
        self, repo_root: Path, branch: str, *, remote: str, timeout_seconds: float
    ) -> remote_push.PushResult:
        self.pushes += 1
        if self.pushes <= self.failures:
            return remote_push.PushResult(
                remote_push.PushStatus.UNREACHABLE, remote, branch, detail="network down"
            )
        return remote_push.push_branch_to_single_remote(
            repo_root, branch, remote=remote, timeout_seconds=timeout_seconds
        )


@pytest.mark.timeout_seconds(30)
def test_a_failed_push_is_retried_with_backoff_and_then_lands(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    target = _target_of(tmp_git_repo)
    bare = _with_origin(tmp_git_repo, target)
    landed = _commit(tmp_git_repo, "work.txt", "work\n", "landed locally")
    client = _FlakyNetworkClient(failures=2)
    clock = _RecordingClock()
    token = install_executors(clock=clock)
    try:
        outcome = push_target_after_landing(
            _config(target),
            tmp_git_repo,
            target,
            RebaseState(fast_forwarded=True, last_target=target),
            remote_client=client,
        )
    finally:
        reset_executors(token)

    assert outcome.last_push_status == "pushed"
    assert client.pushes == 3
    assert list(clock.slept) == [1.0, 2.0]
    assert _run(bare, "rev-parse", target).stdout.strip() == landed
