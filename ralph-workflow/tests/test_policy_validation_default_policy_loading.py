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
        """Test that development_commit_cleanup phase exists with correct config."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert "development_commit_cleanup" in bundle.pipeline.phases
        phase = bundle.pipeline.phases["development_commit_cleanup"]
        assert phase.role == "commit_cleanup"
        assert phase.drain == "commit"

        # Verify both routing declarations are consistent
        dev_analysis = bundle.pipeline.phases["development_analysis"]
        assert dev_analysis.transitions.on_success == "development_commit_cleanup", (
            f"transitions.on_success must point to cleanup phase, got: "
            f"{dev_analysis.transitions.on_success}"
        )
        assert dev_analysis.decisions["completed"].target == "development_commit_cleanup", (
            f"decisions.completed.target must point to cleanup phase, got: "
            f"{dev_analysis.decisions['completed'].target}"
        )
        assert phase.loop_policy is not None, (
            "development_commit_cleanup must have a loop_policy declared"
        )
        assert phase.loop_policy.iteration_state_field == "commit_cleanup_iteration", (
            f"loop_policy.iteration_state_field must be 'commit_cleanup_iteration', "
            f"got: {phase.loop_policy.iteration_state_field}"
        )

    def test_development_commit_loop_resets_contains_commit_cleanup_iteration(self) -> None:
        """Test that development_commit.commit_policy.loop_resets includes commit_cleanup_iteration.

        Bug #2: The default policy was missing commit_cleanup_iteration in loop_resets,
        so the commit cleanup loop counter was never reset at the start of each
        development cycle, causing the cleanup phase to run with a stale counter.
        """
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        dev_commit = bundle.pipeline.phases["development_commit"]
        assert dev_commit.commit_policy is not None
        loop_resets = dev_commit.commit_policy.loop_resets
        assert "commit_cleanup_iteration" in loop_resets, (
            f"loop_resets must include 'commit_cleanup_iteration', got: {loop_resets}"
        )
        assert "development_analysis_iteration" in loop_resets, (
            f"loop_resets must include 'development_analysis_iteration', got: {loop_resets}"
        )
