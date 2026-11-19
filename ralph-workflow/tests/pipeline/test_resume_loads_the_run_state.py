from __future__ import annotations

import json
from pathlib import Path

from ralph.agents.timeout_clock import FakeClock
from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.git.proposal_seal import seal_baseline, seal_pending
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import run_loop
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.recovery.agent_failure_ledger import Cooldown
from ralph.workspace.scope import WorkspaceScope
from tests.pipeline.test_commit_review_rollback_resume import (
    _apply_rejected_proposal,
    _bundle,
    _prepare_workspace,
)
from tests.recovery._scenario_harness import AgentAttemptResult, _build_policy_bundle, run_scenario

_FAILED_TERMINAL = "failed_terminal"
_STALE_RUN_ID = "3a73033871674c57b776de43eba0e88d"
_CURRENT_RUN_ID = "e16393d694954b9b9338f360c3c1f718"
_USER_WORK = "user work that must survive\n"


def _resume_through_the_cli(root: Path) -> PipelineState | None:
    return run_module.load_resume_state(WorkspaceScope(root))


def _policy_state(**fields: object) -> PipelineState:
    base = PipelineState.from_policy(cached_default_policy_bundle().pipeline)
    return base.model_copy(update=fields)


def _stale_agent_checkpoint(root: Path) -> Path:
    return root / ".agent" / "checkpoint.json"


def test_resume_continues_from_the_last_step_not_from_the_initial_state(tmp_path: Path) -> None:
    clock = FakeClock(start=0.0)

    def unproductive(_agent: str, _phase: str, _attempt: int, _ws: object) -> AgentAttemptResult:
        return AgentAttemptResult(produce_artifact=False)

    outcome = run_scenario(
        tmp_path,
        script=unproductive,
        agents=("agent_a", "agent_b"),
        clock=clock,
        stop_after_invocations=3,
    )
    run_state = ckpt.load(ckpt.run_state_path(tmp_path))
    assert run_state is not None
    initial = PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=["agent_a", "agent_b"])},
    )
    ckpt.save(initial, _stale_agent_checkpoint(tmp_path))

    resumed = ckpt.load_for_resume(tmp_path)

    assert resumed is not None
    assert resumed.model_dump() == run_state.model_dump()
    assert resumed.phase == outcome.final_state.phase
    assert resumed.loop_iterations == outcome.final_state.loop_iterations
    assert resumed.outer_progress == outcome.final_state.outer_progress
    assert resumed.agent_cooldowns is not None
    restarted = FakeClock(start=0.0, epoch_start=clock.epoch())
    controller, _cap = run_loop.build_recovery_controller(
        resumed,
        _build_policy_bundle(("agent_a", "agent_b")),
        UnifiedConfig(),
        clock=restarted,
    )
    assert isinstance(controller.agent_eligibility("development", "agent_a"), Cooldown)


def test_a_wt04_shaped_workspace_resumes_the_current_run_and_never_applies_the_stale_rollback(
    tmp_git_repo: Path,
) -> None:
    root = Path(tmp_git_repo)
    readme, _ignored_log, _agent_state = _prepare_workspace(root)
    user_file = root / "user_uncommitted.txt"
    user_file.write_text(_USER_WORK, encoding="utf-8")
    stale_baseline = seal_baseline(root, "0c5898f27c43402dae792058b0dc9737")
    assert stale_baseline is not None
    _apply_rejected_proposal(root, readme)
    stale_proposal = seal_pending(root, "run", "1-1")
    current_baseline = seal_baseline(root, "e129683bc81e45fdb88ce33ab4a7982d", force=True)
    assert current_baseline is not None
    current = _policy_state(
        run_id=_CURRENT_RUN_ID,
        phase="development_commit_review",
        rollback_baseline_ref=current_baseline.ref,
    )
    stale = _policy_state(
        run_id=_STALE_RUN_ID,
        phase="development",
        rollback_pending=True,
        rollback_ref=stale_proposal.ref,
        rollback_baseline_ref=stale_baseline.ref,
    )
    ckpt.save(current, ckpt.run_state_path(root))
    ckpt.save(stale, _stale_agent_checkpoint(root))

    loaded = _resume_through_the_cli(root)
    assert loaded is not None
    resumed = run_loop.resolve_initial_state(
        config=UnifiedConfig(),
        policy_bundle=_bundle(),
        initial_state=loaded,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
        workspace_root=root,
    )

    assert resumed.run_id == _CURRENT_RUN_ID
    assert resumed.phase == "development_commit_review"
    assert resumed.phase != _FAILED_TERMINAL
    assert resumed.rollback_pending is False
    assert readme.read_text(encoding="utf-8") == "developer change\n"
    assert (root / "rejected_added.txt").exists()
    assert user_file.read_text(encoding="utf-8") == _USER_WORK
    assert not _stale_agent_checkpoint(root).exists()
    assert not ckpt.rollback_marker_path(root).exists()


def test_a_legacy_marker_of_the_resumed_run_becomes_the_rollback_marker(tmp_path: Path) -> None:
    pending = PipelineState(
        run_id=_CURRENT_RUN_ID,
        phase="development",
        rollback_pending=True,
        rollback_ref="refs/ralph/proposals/run/1-1",
    )
    ckpt.save(pending, ckpt.run_state_path(tmp_path))
    ckpt.save(pending, _stale_agent_checkpoint(tmp_path))

    resumed = ckpt.load_for_resume(tmp_path)

    assert resumed is not None
    assert resumed.run_id == _CURRENT_RUN_ID
    assert not _stale_agent_checkpoint(tmp_path).exists()
    marker = ckpt.load(ckpt.rollback_marker_path(tmp_path))
    assert marker is not None
    assert marker.rollback_ref == pending.rollback_ref


def test_a_workspace_with_only_the_legacy_checkpoint_still_resumes(tmp_path: Path) -> None:
    ckpt.save(_policy_state(phase="planning"), _stale_agent_checkpoint(tmp_path))

    resumed = _resume_through_the_cli(tmp_path)

    assert resumed is not None
    assert resumed.phase == "planning"


def test_an_old_format_run_state_without_run_id_resumes(tmp_path: Path) -> None:
    path = ckpt.run_state_path(tmp_path)
    ckpt.save(_policy_state(phase="development"), path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    for field in ("run_id", "rollback_pending", "rollback_ref", "rollback_baseline_ref"):
        raw.pop(field, None)
    path.write_text(json.dumps(raw), encoding="utf-8")

    resumed = _resume_through_the_cli(tmp_path)

    assert resumed is not None
    assert resumed.phase == "development"


def test_an_interrupt_marks_the_last_step_state_instead_of_rewinding_to_the_initial_state(
    tmp_path: Path,
) -> None:
    initial = PipelineState(phase="planning")
    stepped = initial.copy_with(phase="development_commit_review")
    ckpt.save(stepped, ckpt.run_state_path(tmp_path))

    run_module.save_interrupt_checkpoint(tmp_path, initial)

    persisted = ckpt.load(ckpt.run_state_path(tmp_path))
    assert persisted is not None
    assert persisted.phase == "development_commit_review"
    assert persisted.interrupted_by_user is True
