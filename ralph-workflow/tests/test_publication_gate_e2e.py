from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from git import Repo

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.publication_gate import PublicationBlock
from ralph.pipeline.rebase_state import RebaseState
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
