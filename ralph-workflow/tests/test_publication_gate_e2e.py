from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from git import Repo
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline import run_loop
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.publication_gate import PublicationBlock, last_publication_gate_result
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.subprocess_e2e

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


def _feature(tmp_git_repo: Path, tmp_path: Path, *, declare_gate: bool) -> tuple[str, Path]:
    target = (
        _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
        .stdout.strip()
        .removeprefix("refs/heads/")
    )
    if declare_gate:
        _commit(tmp_git_repo, str(_POLICY), f"# Verification\n\n{_GATE_FACT}", "declare gate")
    feature = tmp_path / "feature"
    assert _run(tmp_git_repo, "worktree", "add", "-b", "feature", str(feature)).returncode == 0
    return target, feature


def _integrate(feature: Path, target: str) -> RebaseState:
    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(feature),
        RebaseState(),
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )
    assert outcome is not None
    return outcome


@pytest.mark.timeout_seconds(30)
def test_a_red_gate_keeps_the_run_commits_off_the_target(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    target, feature = _feature(tmp_git_repo, tmp_path, declare_gate=True)
    before = branch_sha(tmp_git_repo, target)
    feature_tip = _commit(feature, "work.txt", "work\n", "run commit without gate-ok")

    outcome = _integrate(feature, target)

    assert outcome.fast_forwarded is False
    assert outcome.publication_block is PublicationBlock.GATE_FAILED
    assert outcome.last_reason is not None
    assert "test -f gate-ok" in outcome.last_reason
    assert branch_sha(tmp_git_repo, target) == before
    assert _run(feature, "rev-parse", "HEAD").stdout.strip() == feature_tip


@pytest.mark.timeout_seconds(30)
def test_a_green_gate_publishes_the_run_commits(tmp_git_repo: Path, tmp_path: Path) -> None:
    target, feature = _feature(tmp_git_repo, tmp_path, declare_gate=True)
    feature_tip = _commit(feature, "gate-ok", "ok\n", "run commit that passes the gate")

    outcome = _integrate(feature, target)

    assert outcome.fast_forwarded is True
    assert outcome.publication_block is None
    assert branch_sha(tmp_git_repo, target) == feature_tip


@pytest.mark.timeout_seconds(30)
def test_a_fix_on_a_new_tree_reruns_the_gate_and_publishes(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    target, feature = _feature(tmp_git_repo, tmp_path, declare_gate=True)
    _commit(feature, "work.txt", "work\n", "red run commit")
    assert _integrate(feature, target).fast_forwarded is False

    fixed_tip = _commit(feature, "gate-ok", "ok\n", "fix the gate")
    outcome = _integrate(feature, target)

    assert outcome.fast_forwarded is True
    assert branch_sha(tmp_git_repo, target) == fixed_tip


@pytest.mark.timeout_seconds(30)
def test_a_project_without_a_declared_gate_publishes_as_before(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    target, feature = _feature(tmp_git_repo, tmp_path, declare_gate=False)
    feature_tip = _commit(feature, "work.txt", "work\n", "run commit")

    outcome = _integrate(feature, target)

    assert outcome.fast_forwarded is True
    assert outcome.publication_block is None
    assert branch_sha(tmp_git_repo, target) == feature_tip


def _counting_red_gate(tmp_path: Path) -> tuple[str, Path]:
    runs = tmp_path / "gate-runs.log"
    fact = f"RALPH-FACT: authoritative_verify_command: `echo run >> {runs}; exit 2`\n"
    return fact, runs


def _feature_with_gate(tmp_git_repo: Path, tmp_path: Path, fact: str) -> tuple[str, Path]:
    target = (
        _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
        .stdout.strip()
        .removeprefix("refs/heads/")
    )
    _commit(tmp_git_repo, str(_POLICY), f"# Verification\n\n{fact}", "declare gate")
    feature = tmp_path / "feature"
    assert _run(tmp_git_repo, "worktree", "add", "-b", "feature", str(feature)).returncode == 0
    _commit(feature, "work.txt", "work\n", "red run commit")
    return target, feature


@pytest.mark.timeout_seconds(60)
def test_an_unchanged_blocked_tree_is_evaluated_and_landed_at_most_once(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    fact, runs = _counting_red_gate(tmp_path)
    target, feature = _feature_with_gate(tmp_git_repo, tmp_path, fact)
    before = branch_sha(tmp_git_repo, target)
    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )
    refused_landings: list[str] = []
    sink = logger.add(
        lambda message: refused_landings.append(str(message)),
        level="CRITICAL",
        format="{message}",
    )
    try:
        outcomes = [
            integrate_before_planning(config, WorkspaceScope(feature), RebaseState())
            for _tick in range(10)
        ]
    finally:
        logger.remove(sink)

    assert runs.read_text(encoding="utf-8").splitlines() == ["run"]
    assert len([line for line in refused_landings if "did NOT land" in line]) <= 1
    assert branch_sha(tmp_git_repo, target) == before
    assert all(
        outcome is not None and outcome.publication_block is PublicationBlock.GATE_FAILED
        for outcome in outcomes
    )
    gate = last_publication_gate_result(feature)
    assert gate is not None
    assert gate.consecutive_failures == 1


@pytest.mark.timeout_seconds(60)
def test_planning_proceeds_past_a_gate_blocked_tree_instead_of_waiting(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    fact, runs = _counting_red_gate(tmp_path)
    target, feature = _feature_with_gate(tmp_git_repo, tmp_path, fact)
    before = branch_sha(tmp_git_repo, target)
    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )
    blocked = _integrate(feature, target)
    assert blocked.publication_block is PublicationBlock.GATE_FAILED
    ctx = SimpleNamespace(
        config=config,
        workspace_scope=WorkspaceScope(feature),
        policy_bundle=cached_default_policy_bundle(),
        active_display=None,
    )

    for _tick in range(5):
        _state, held = run_loop.ensure_planning_in_sync(
            PipelineState(phase="planning", rebase=blocked), ctx, "planning"
        )
        assert held is None

    assert runs.read_text(encoding="utf-8").splitlines() == ["run"]
    assert branch_sha(tmp_git_repo, target) == before
