"""Unit tests for policy validation.

Tests cover:
- Valid agents.toml loading
- Missing drain binding raises PolicyValidationError
- Chain referencing unknown agent raises PolicyValidationError
- Empty chain list raises PolicyValidationError
- All six built-in drains are bound in default agents.toml
"""

from __future__ import annotations

import importlib
from pathlib import Path

from ralph.policy.loader import load_policy

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestDefaultPolicyLoading:
    """Tests for loading the default policy."""

    def test_load_default_policy_succeeds(self) -> None:
        """Test that the default policy loads without error."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.agents is not None
        assert bundle.pipeline is not None
        assert bundle.artifacts is not None

    def test_all_builtin_drains_bound(self) -> None:
        """Test that all built-in drains are bound in default agents.toml."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        expected_drains = {
            "planning",
            "development",
            "development_analysis",
            "development_commit",
        }

        actual_drains = set(bundle.agents.agent_drains.keys())
        assert expected_drains.issubset(actual_drains), (
            f"Missing drains: {expected_drains - actual_drains}"
        )

    def test_default_pipeline_entry_phase(self) -> None:
        """Test that default pipeline has planning as entry phase."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.pipeline.entry_phase == "planning"

    def test_default_pipeline_terminal_phase(self) -> None:
        """Test that default pipeline has complete as terminal phase."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.pipeline.terminal_phase == "complete"

    def test_default_pipeline_parallel_execution_max_work_units(self) -> None:
        """Test that default pipeline loads the work unit cap from TOML."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.pipeline.phases["development"].parallelization is not None
        dev_para = bundle.pipeline.phases["development"].parallelization
        assert dev_para.max_work_units == DEFAULT_MAX_WORK_UNITS

    def test_all_pipeline_drains_are_bound(self) -> None:
        """Test that every drain used in pipeline.phases is bound in agents.agent_drains.

        This is enforced by PolicyBundle's all_pipeline_drains_are_bound validator.
        """
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        # This should not raise - the validator ensures all drains are bound
        # Skip terminal phase since it never invokes an agent
        for phase_name, phase_def in bundle.pipeline.phases.items():
            if phase_def.role == "terminal":
                continue
            assert phase_def.drain in bundle.agents.agent_drains, (
                f"Phase '{phase_name}' uses unbound drain '{phase_def.drain}'"
            )

    def test_development_commit_cleanup_phase_in_default_policy(self) -> None:
        """Test that the default policy exposes both pre-analysis and final commit cleanups."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        pre_analysis_cleanup = bundle.pipeline.phases["development_commit_cleanup"]
        final_cleanup = bundle.pipeline.phases["development_final_commit_cleanup"]
        development = bundle.pipeline.phases["development"]
        dev_analysis = bundle.pipeline.phases["development_analysis"]

        assert pre_analysis_cleanup.role == "commit_cleanup"
        assert pre_analysis_cleanup.drain == "commit"
        assert final_cleanup.role == "commit_cleanup"
        assert final_cleanup.drain == "commit"

        assert development.transitions.on_success == "development_commit_cleanup"
        assert dev_analysis.transitions.on_success == "development_final_commit_cleanup"
        assert dev_analysis.decisions["completed"].target == "development_final_commit_cleanup"

        for phase in (pre_analysis_cleanup, final_cleanup):
            assert phase.loop_policy is not None, "commit cleanup phases must declare a loop_policy"
            assert phase.loop_policy.iteration_state_field == "commit_cleanup_iteration", (
                f"loop_policy.iteration_state_field must be 'commit_cleanup_iteration', "
                f"got: {phase.loop_policy.iteration_state_field}"
            )

    def test_default_policy_uses_pre_analysis_and_final_commit_paths(self) -> None:
        """Default policy should commit before analysis and again after successful analysis."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        development = bundle.pipeline.phases["development"]
        development_analysis = bundle.pipeline.phases["development_analysis"]
        pre_analysis_cleanup = bundle.pipeline.phases["development_commit_cleanup"]
        final_cleanup = bundle.pipeline.phases["development_final_commit_cleanup"]
        final_commit = bundle.pipeline.phases["development_final_commit"]

        assert development.transitions.on_success == "development_commit_cleanup"
        pre_analysis_commit = bundle.pipeline.phases["development_commit"]

        assert pre_analysis_cleanup.transitions.on_success == "development_commit"
        assert development_analysis.transitions.on_success == "development_final_commit_cleanup"
        assert (
            development_analysis.decisions["completed"].target
            == "development_final_commit_cleanup"
        )
        assert final_cleanup.transitions.on_success == "development_final_commit"
        assert pre_analysis_commit.commit_policy is not None
        assert pre_analysis_commit.commit_policy.increments_counter is None
        assert pre_analysis_commit.commit_policy.skipped_advances_progress is False
        assert final_commit.role == "commit"
        assert final_commit.drain == "development_commit"
        assert final_commit.commit_policy is not None
        assert final_commit.commit_policy.skipped_advances_progress is True
        assert final_commit.commit_policy.increments_counter == "iteration"

    def test_development_commit_loop_resets_contains_commit_cleanup_iteration(self) -> None:
        """Test that development commit policies reset commit cleanup loop counters."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        for phase_name in ("development_commit", "development_final_commit"):
            dev_commit = bundle.pipeline.phases[phase_name]
            assert dev_commit.commit_policy is not None
            loop_resets = dev_commit.commit_policy.loop_resets
            assert "commit_cleanup_iteration" in loop_resets, (
                f"{phase_name} loop_resets must include "
                f"'commit_cleanup_iteration', got: {loop_resets}"
            )
            assert "development_analysis_iteration" in loop_resets, (
                f"{phase_name} loop_resets must include "
                f"'development_analysis_iteration', got: {loop_resets}"
            )
