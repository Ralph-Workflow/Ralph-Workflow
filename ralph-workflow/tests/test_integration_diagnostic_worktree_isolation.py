"""Linked-worktree regression coverage for the diagnostic evidence collector.

Acceptance scenario 1: in a repository with linked worktrees, an
unrelated branch advancing in one worktree must not invalidate the
retained-integration obligation in another worktree. The new
SubprocessOperationEvidenceCollector is the architectural fix:
it never reads repository-global refs or common-dir reflogs across worktrees.

Each test drives the real recovery boundary: imports and calls
ralph.pipeline._integration_diagnostic.diagnose_retained_integration with
a real SubprocessOperationEvidenceCollector, asserting the durable record's
intervention flag and the run-loop's PhaseDecision.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline._integration_diagnostic import (
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    diagnose_retained_integration,
    set_operation_evidence_collector,
)
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import (
    START_INTEGRATING,
    IntegrationRecord,
    read_record,
    record_path,
    write_record,
)
from ralph.pipeline.operation_evidence import SubprocessOperationEvidenceCollector
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.scope import WorkspaceScope


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "user.email", "test@example.com")
    (root / "README.md").write_text("test")
    _git(root, "add", "README.md")
    _git(root, "commit", "--quiet", "-m", "initial")
    return root


def _add_worktree(primary: Path, path: Path, branch: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(primary, "worktree", "add", str(path), "-b", branch)
    return path


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


def _build_loop_ctx(primary: Path, policy: PipelinePolicy) -> MagicMock:
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": policy})
    empty_registry: dict[str, object] = {}
    ctx = MagicMock()
    ctx.workspace_scope = WorkspaceScope(primary)
    ctx.config = UnifiedConfig()
    ctx.pipeline_deps = MagicMock()
    ctx.policy_bundle = bundle
    ctx.registry = empty_registry
    return ctx


@pytest.mark.subprocess_e2e
def test_unrelated_branch_advance_does_not_invalidate_evidence(tmp_path: Path) -> None:
    """Linked-worktree scenario: unrelated branch advance drives real recovery boundary."""
    primary = _init_repo(tmp_path / "primary")
    other = _add_worktree(primary, tmp_path / "other", branch="unrelated")

    head_sha = _git(primary, "rev-parse", "HEAD").stdout.strip()
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=head_sha,
        pre_target_sha=head_sha,
        owning_worktree=str(primary),
    ).with_transition(START_INTEGRATING)
    write_record(primary, record)

    collector = SubprocessOperationEvidenceCollector()
    set_operation_evidence_collector(collector)

    # Advance the unrelated branch in the linked worktree
    (other / "new.txt").write_text("new content")
    _git(other, "add", "new.txt")
    _git(other, "commit", "--quiet", "-m", "advance unrelated")

    policy = _policy()
    ctx = _build_loop_ctx(primary, policy)

    # Drive the real recovery boundary
    result = diagnose_retained_integration(ctx, failure="simulated diagnostic recovery failure")
    assert result is False

    # Assert durable record on disk is intact
    assert record_path(primary).exists()
    persisted = read_record(primary)
    assert persisted is not None
    assert persisted.diagnostic_intervention_required is False

    # Assert run-loop's PhaseDecision evaluates to BLOCK_FOR_RECOVERY
    state = PipelineState(phase="development", rebase=RebaseState())
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=persisted,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY


@pytest.mark.subprocess_e2e
def test_branch_reflog_change_does_not_invalidate_evidence(tmp_path: Path) -> None:
    """Commondir reflog changes from linked worktree do not break recovery boundary."""
    primary = _init_repo(tmp_path / "primary")
    other = _add_worktree(primary, tmp_path / "other", branch="unrelated")

    head_sha = _git(primary, "rev-parse", "HEAD").stdout.strip()
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=head_sha,
        pre_target_sha=head_sha,
        owning_worktree=str(primary),
    ).with_transition(START_INTEGRATING)
    write_record(primary, record)

    collector = SubprocessOperationEvidenceCollector()
    set_operation_evidence_collector(collector)

    for i in range(3):
        (other / f"file_{i}.txt").write_text(f"content {i}")
        _git(other, "add", f"file_{i}.txt")
        _git(other, "commit", "--quiet", "-m", f"reflog update {i}")

    policy = _policy()
    ctx = _build_loop_ctx(primary, policy)

    # Drive the real recovery boundary
    diagnose_retained_integration(ctx, failure="reflog test failure")

    persisted = read_record(primary)
    assert persisted is not None
    assert persisted.diagnostic_intervention_required is False

    state = PipelineState(phase="development", rebase=RebaseState())
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=persisted,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY


@pytest.mark.subprocess_e2e
def test_exhausted_diagnostic_attempts_sets_intervention_and_blocks(tmp_path: Path) -> None:
    """Exhausted diagnostic attempts in linked worktree drive REPORT_INTERVENTION_REQUIRED."""
    primary = _init_repo(tmp_path / "primary")
    _add_worktree(primary, tmp_path / "other", branch="unrelated")

    head_sha = _git(primary, "rev-parse", "HEAD").stdout.strip()
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=head_sha,
        pre_target_sha=head_sha,
        merge_commit_repair_attempts=DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
        owning_worktree=str(primary),
    ).with_transition(START_INTEGRATING)
    write_record(primary, record)

    collector = SubprocessOperationEvidenceCollector()
    set_operation_evidence_collector(collector)

    policy = _policy()
    ctx = _build_loop_ctx(primary, policy)

    diagnose_retained_integration(ctx, failure="exhausted attempts cap test")

    persisted = read_record(primary)
    assert persisted is not None
    assert persisted.diagnostic_intervention_required is True

    state = PipelineState(phase="development", rebase=RebaseState())
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=persisted,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.REPORT_INTERVENTION_REQUIRED
