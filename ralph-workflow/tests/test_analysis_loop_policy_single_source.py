"""Contract tests for single-source analysis loop caps.

Analysis phases must derive their cap from pipeline.loop_counters only.
The phase-local loop_policy block keeps only the counter linkage and must not
accept a duplicate max_iterations field.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseLoopPolicy
from ralph.policy.validation import PolicyValidationError

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_DIR = ROOT / "ralph" / "policy" / "defaults"
PLANNING_ANALYSIS_DEFAULT_MAX = 1
DEVELOPMENT_ANALYSIS_DEFAULT_MAX = 10


def test_phase_loop_policy_uses_counter_link_only() -> None:
    cfg = PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration")

    assert cfg.iteration_state_field == "planning_analysis_iteration"
    assert not hasattr(cfg, "max_iterations")


def test_default_policy_uses_loop_counters_as_single_source_of_truth() -> None:
    bundle = load_policy(DEFAULTS_DIR)

    planning_analysis = bundle.pipeline.phases["planning_analysis"]
    development_analysis = bundle.pipeline.phases["development_analysis"]

    assert planning_analysis.loop_policy is not None
    assert development_analysis.loop_policy is not None
    assert planning_analysis.loop_policy.iteration_state_field == "planning_analysis_iteration"
    assert (
        development_analysis.loop_policy.iteration_state_field == "development_analysis_iteration"
    )
    assert not hasattr(planning_analysis.loop_policy, "max_iterations")
    assert not hasattr(development_analysis.loop_policy, "max_iterations")
    assert (
        bundle.pipeline.loop_counters["planning_analysis_iteration"].default_max
        == PLANNING_ANALYSIS_DEFAULT_MAX
    )
    assert (
        bundle.pipeline.loop_counters["development_analysis_iteration"].default_max
        == DEVELOPMENT_ANALYSIS_DEFAULT_MAX
    )


def test_loader_rejects_removed_phase_local_analysis_cap(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent"
    config_dir.mkdir()
    (config_dir / "pipeline.toml").write_text(
        """
entry_phase = "planning"
terminal_phase = "complete"

[loop_counters.planning_analysis_iteration]
default_max = 2

[phases.planning]
drain = "planning"
role = "execution"
[phases.planning.transitions]
on_success = "planning_analysis"

[phases.planning_analysis]
drain = "planning_analysis"
role = "analysis"
prompt_template = "planning_analysis.jinja"
[phases.planning_analysis.transitions]
on_success = "complete"
on_loopback = "planning"
[phases.planning_analysis.loop_policy]
max_iterations = 2
iteration_state_field = "planning_analysis_iteration"
[phases.planning_analysis.decisions.completed]
target = "complete"
reset_loop = true
[phases.planning_analysis.decisions.request_changes]
target = "planning"
reset_loop = false
[phases.planning_analysis.decisions.failed]
target = "planning"
reset_loop = false

[phases.complete]
drain = "complete"
role = "terminal"
terminal_outcome = "success"
[phases.complete.transitions]
on_success = "complete"

[phases.failed_terminal]
drain = "complete"
role = "terminal"
terminal_outcome = "failure"
[phases.failed_terminal.transitions]
on_success = "failed_terminal"

[recovery]
failed_route = "failed_terminal"
"""
    )

    with pytest.raises((PolicyValidationError, ValidationError)) as exc_info:
        load_policy(config_dir)

    assert "max_iterations" in str(exc_info.value)
