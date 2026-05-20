"""Loader tests for block-authored pipeline policies."""

from __future__ import annotations

import shutil
from pathlib import Path
from textwrap import dedent

import pytest

from ralph.policy.loader import load_policy
from ralph.policy.validation import PolicyValidationError


def _copy_default_policy_files(target_dir: Path) -> None:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    target_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("agents.toml", "artifacts.toml"):
        shutil.copy(defaults_dir / filename, target_dir / filename)


def test_load_policy_compiles_group_and_individual_blocks_into_runtime_phases(
    tmp_path: Path,
) -> None:
    _copy_default_policy_files(tmp_path)
    (tmp_path / "pipeline.toml").write_text(
        dedent(
            """
            entry_block = "developer_iteration"
            terminal_phase = "complete"

            [loop_counters.development_analysis_iteration]
            default_max = 10
            description = "Development analysis loop iteration counter"

            [budget_counters.iteration]
            description = "Development iteration counter"
            tracks_budget = true
            default_max = 5

            [blocks.developer_iteration]
            kind = "group"
            child_blocks = [
              "planning",
              "planning_analysis",
              "development",
              "development_analysis",
              "development_pre_commit",
              "development_final_commit",
              "complete",
              "failed_terminal",
            ]
            completion_block = "development_final_commit"
            before_complete = ["development_pre_commit"]
            increments_counter = "iteration"
            loop_resets = ["development_analysis_iteration"]

            [blocks.planning]
            kind = "individual"
            phase_name = "planning"
            [blocks.planning.phase]
            drain = "planning"
            role = "execution"
            prompt_template = "planning.jinja"
            [blocks.planning.phase.transitions]
            on_success = "planning_analysis"

            [blocks.planning_analysis]
            kind = "individual"
            phase_name = "planning_analysis"
            [blocks.planning_analysis.phase]
            drain = "planning_analysis"
            role = "analysis"
            prompt_template = "planning_analysis.jinja"
            [blocks.planning_analysis.phase.transitions]
            on_success = "development"
            on_loopback = "planning"
            [blocks.planning_analysis.phase.loop_policy]
            iteration_state_field = "development_analysis_iteration"
            [blocks.planning_analysis.phase.decisions.completed]
            target = "development"
            reset_loop = true
            [blocks.planning_analysis.phase.decisions.request_changes]
            target = "planning"
            reset_loop = false
            [blocks.planning_analysis.phase.decisions.failed]
            target = "planning"
            reset_loop = false

            [blocks.development]
            kind = "individual"
            phase_name = "development"
            [blocks.development.phase]
            drain = "development"
            role = "execution"
            prompt_template = "developer_iteration.jinja"
            [blocks.development.phase.transitions]
            on_success = "development_analysis"
            on_loopback = "development"

            [blocks.development_analysis]
            kind = "individual"
            phase_name = "development_analysis"
            [blocks.development_analysis.phase]
            drain = "development_analysis"
            role = "analysis"
            prompt_template = "development_analysis.jinja"
            [blocks.development_analysis.phase.transitions]
            on_success = "development_pre_commit"
            on_loopback = "development"
            [blocks.development_analysis.phase.loop_policy]
            iteration_state_field = "development_analysis_iteration"
            [blocks.development_analysis.phase.decisions.completed]
            target = "development_pre_commit"
            reset_loop = true
            [blocks.development_analysis.phase.decisions.request_changes]
            target = "development"
            reset_loop = false
            [blocks.development_analysis.phase.decisions.failed]
            target = "development"
            reset_loop = false

            [blocks.development_pre_commit]
            kind = "individual"
            phase_name = "development_pre_commit"
            [blocks.development_pre_commit.phase]
            drain = "development_commit"
            role = "commit"
            prompt_template = "commit_message.jinja"
            [blocks.development_pre_commit.phase.transitions]
            on_success = "development_final_commit"
            on_failure = "failed_terminal"
            [blocks.development_pre_commit.phase.commit_policy]
            requires_artifact = true
            skipped_advances_progress = false

            [blocks.development_final_commit]
            kind = "individual"
            phase_name = "development_final_commit"
            [blocks.development_final_commit.phase]
            drain = "development_commit"
            role = "commit"
            prompt_template = "commit_message.jinja"
            [blocks.development_final_commit.phase.transitions]
            on_success = "complete"
            on_failure = "failed_terminal"
            [blocks.development_final_commit.phase.commit_policy]
            requires_artifact = true
            skipped_advances_progress = false

            [blocks.complete]
            kind = "individual"
            phase_name = "complete"
            [blocks.complete.phase]
            drain = "complete"
            role = "terminal"
            terminal_outcome = "success"
            [blocks.complete.phase.transitions]
            on_success = "complete"
            on_loopback = "complete"

            [blocks.failed_terminal]
            kind = "individual"
            phase_name = "failed_terminal"
            [blocks.failed_terminal.phase]
            drain = "failed_terminal"
            role = "terminal"
            terminal_outcome = "failure"
            [blocks.failed_terminal.phase.transitions]
            on_success = "failed_terminal"
            on_loopback = "failed_terminal"

            [recovery]
            failed_route = "failed_terminal"
            terminal_failure_phase = "failed_terminal"
            """
        ),
        encoding="utf-8",
    )

    bundle = load_policy(tmp_path)

    assert bundle.pipeline.entry_block == "developer_iteration"
    assert bundle.pipeline.entry_phase == "planning"
    assert set(bundle.pipeline.phases) >= {
        "planning",
        "planning_analysis",
        "development",
        "development_analysis",
        "development_pre_commit",
        "development_final_commit",
        "complete",
        "failed_terminal",
    }
    lifecycle = bundle.pipeline.lifecycle_phases["development_final_commit"]
    assert lifecycle.lifecycle_name == "developer_iteration"
    assert lifecycle.increments_counter == "iteration"
    assert lifecycle.loop_resets == ["development_analysis_iteration"]
    assert lifecycle.before_complete == ["development_pre_commit"]
    assert lifecycle.after_complete == []


def test_load_policy_rejects_group_blocks_with_unknown_completion_block(tmp_path: Path) -> None:
    _copy_default_policy_files(tmp_path)
    (tmp_path / "pipeline.toml").write_text(
        dedent(
            """
            entry_block = "developer_iteration"
            terminal_phase = "complete"

            [budget_counters.iteration]
            description = "Development iteration counter"
            tracks_budget = true
            default_max = 5

            [blocks.developer_iteration]
            kind = "group"
            child_blocks = ["planning", "complete", "failed_terminal"]
            completion_block = "missing_block"
            increments_counter = "iteration"

            [blocks.planning]
            kind = "individual"
            phase_name = "planning"
            [blocks.planning.phase]
            drain = "planning"
            role = "execution"
            [blocks.planning.phase.transitions]
            on_success = "complete"

            [blocks.complete]
            kind = "individual"
            phase_name = "complete"
            [blocks.complete.phase]
            drain = "complete"
            role = "terminal"
            terminal_outcome = "success"
            [blocks.complete.phase.transitions]
            on_success = "complete"
            on_loopback = "complete"

            [blocks.failed_terminal]
            kind = "individual"
            phase_name = "failed_terminal"
            [blocks.failed_terminal.phase]
            drain = "failed_terminal"
            role = "terminal"
            terminal_outcome = "failure"
            [blocks.failed_terminal.phase.transitions]
            on_success = "failed_terminal"
            on_loopback = "failed_terminal"

            [recovery]
            failed_route = "failed_terminal"
            terminal_failure_phase = "failed_terminal"
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(PolicyValidationError, match="completion_block"):
        load_policy(tmp_path)
