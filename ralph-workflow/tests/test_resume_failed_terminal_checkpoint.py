from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.git.operations import stage_files
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import run_loop
from ralph.pipeline.effects import PreparePromptEffect
from ralph.pipeline.orchestrator import determine_next_effect
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.timeout_seconds(10)]

_HOOK_REJECTION = (
    "GitOperationError: Git create_commit failed: social-proof verifier\n"
    "FAIL: 1 banned pattern(s) found."
)


def _stage_work(root: Path) -> None:
    FsWorkspace(root).write("feature.txt", "four cycles of staged work\n")
    stage_files(root, ["feature.txt"])


def _failed_checkpoint(root: Path, **fields: object) -> PipelineState:
    bundle = cached_default_policy_bundle()
    failed_route = bundle.pipeline.recovery.failed_route
    defaults: dict[str, object] = {
        "phase": failed_route,
        "current_drain": failed_route,
        "previous_phase": "development_commit",
        "policy_entry_phase": bundle.pipeline.entry_phase,
        "policy_format_version": 2,
        "recovery_epoch": 3,
        "last_error": _HOOK_REJECTION,
        "last_execution_result_status": "failed",
        "approved_tree_sha": "0" * 40,
        "commit_approval_required": True,
    }
    defaults.update(fields)
    ckpt.save(PipelineState.model_validate(defaults), ckpt.run_state_path(root))
    loaded = ckpt.load_for_resume(root)
    assert loaded is not None
    return loaded


def _resume(root: Path, state: PipelineState | None) -> PipelineState:
    return run_loop.resolve_initial_state(
        UnifiedConfig(),
        cached_default_policy_bundle(),
        state,
        None,
        None,
        None,
        None,
        workspace_root=root,
    )


def _resume_capturing_warnings(root: Path, state: PipelineState) -> tuple[PipelineState, list[str]]:
    messages = io.StringIO()
    sink = logger.add(messages, level="WARNING", format="{message}")
    try:
        resumed = _resume(root, state)
    finally:
        logger.remove(sink)
    return resumed, messages.getvalue().splitlines()


def test_resuming_a_run_that_failed_in_its_commit_reopens_the_failed_commit(
    tmp_git_repo: Path,
) -> None:
    _stage_work(tmp_git_repo)
    checkpoint = _failed_checkpoint(tmp_git_repo)

    resumed, warnings = _resume_capturing_warnings(tmp_git_repo, checkpoint)

    bundle = cached_default_policy_bundle()
    assert resumed.phase == "development_commit"
    assert resumed.phase not in bundle.pipeline.terminal_states()
    assert resumed.recovery_epoch == 0
    next_effect = determine_next_effect(resumed, bundle.pipeline, bundle.agents)
    assert isinstance(next_effect, PreparePromptEffect)
    assert next_effect.phase == "development_commit"
    reopen_warnings = [line for line in warnings if "development_commit" in line]
    assert len(reopen_warnings) == 1
    assert "social-proof verifier" in reopen_warnings[0]


def test_resuming_a_failed_run_with_no_recorded_phase_commits_its_staged_work(
    tmp_git_repo: Path,
) -> None:
    _stage_work(tmp_git_repo)
    failed_route = cached_default_policy_bundle().pipeline.recovery.failed_route
    checkpoint = _failed_checkpoint(tmp_git_repo, previous_phase=failed_route)

    resumed = _resume(tmp_git_repo, checkpoint)

    assert resumed.phase == "development_final_commit_review"
    assert resumed.recovery_epoch == 0
    assert resumed.commit_approval_required is False
    assert resumed.approved_tree_sha is None


def test_resuming_a_failed_run_with_no_recorded_phase_and_no_work_stays_failed(
    tmp_git_repo: Path,
) -> None:
    failed_route = cached_default_policy_bundle().pipeline.recovery.failed_route
    checkpoint = _failed_checkpoint(tmp_git_repo, previous_phase=failed_route)

    resumed = _resume(tmp_git_repo, checkpoint)

    assert resumed.phase == failed_route


def test_resuming_a_run_that_used_every_recovery_cycle_gets_a_fresh_cycle_budget(
    tmp_git_repo: Path,
) -> None:
    checkpoint = _failed_checkpoint(
        tmp_git_repo,
        previous_phase="development",
        recovery_cycle_count=200,
        recovery_cycle_cap=200,
    )

    resumed = _resume(tmp_git_repo, checkpoint)

    assert resumed.phase == "development"
    assert resumed.recovery_cycle_count == 0
    assert resumed.recovery_cycle_cap == 200


def test_resuming_a_failed_run_keeps_a_recovery_cycle_count_below_the_cap(
    tmp_git_repo: Path,
) -> None:
    checkpoint = _failed_checkpoint(
        tmp_git_repo, previous_phase="development", recovery_cycle_count=7
    )

    resumed = _resume(tmp_git_repo, checkpoint)

    assert resumed.phase == "development"
    assert resumed.recovery_cycle_count == 7


def test_a_fresh_run_still_starts_at_the_policy_entry_phase(tmp_git_repo: Path) -> None:
    resumed = _resume(tmp_git_repo, None)

    assert resumed.phase == cached_default_policy_bundle().pipeline.entry_phase


def test_resuming_a_completed_run_with_nothing_left_still_finishes(tmp_git_repo: Path) -> None:
    bundle = cached_default_policy_bundle()
    completed = PipelineState(
        phase=bundle.pipeline.terminal_phase,
        current_drain=bundle.pipeline.terminal_phase,
        previous_phase="development_final_commit",
    )

    resumed = _resume(tmp_git_repo, completed)

    assert resumed.phase == bundle.pipeline.terminal_phase
