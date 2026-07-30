"""Unit tests for agents-policy construction and the bundled default policy.

Split out of ``test_policy_loader.py`` (repo structure policy caps a file at
1000 lines): this half pins what the shipped defaults guarantee, the other half
pins loading and validation errors.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from textwrap import dedent

import pytest
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.policy.loader import (
    PolicyValidationError as LoaderPolicyValidationError,
)
from ralph.policy.loader import (
    build_agents_policy_from_config,
    load_policy,
)

PLANNING_ANALYSIS_DEFAULT_MAX_ITERATIONS = 0


def _copy_default_policy_files(target_dir: Path) -> None:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    target_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("agents.toml", "pipeline.toml", "artifacts.toml"):
        shutil.copy(defaults_dir / filename, target_dir / filename)


def test_policy_loader_unknown_top_level_table_warns_with_file(
    tmp_path: Path,
) -> None:
    """A misspelled policy table must be visible while open subtables remain untouched."""
    config_dir = tmp_path / ".agent"
    _copy_default_policy_files(config_dir)
    pipeline_path = config_dir / "pipeline.toml"
    pipeline_path.write_text(
        pipeline_path.read_text(encoding="utf-8") + "\n[loop_counterz]\nmax = 1\n",
        encoding="utf-8",
    )
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        load_policy(config_dir)
    finally:
        logger.remove(sink_id)

    warning = "\n".join(records)
    assert "loop_counterz" in warning
    assert str(pipeline_path) in warning


def test_build_agents_policy_from_config_rejects_missing_drain(tmp_path: Path) -> None:
    """A pipeline drain that neither the user config nor the bundled
    defaults bind must cause a cross-policy validation failure at load
    time — no sibling inference. Drains the user omits but the defaults
    define are satisfied by the bundled binding (standard layering).
    """
    config_dir = tmp_path / ".agent"
    config_dir.mkdir(parents=True)

    config = UnifiedConfig(
        agent_chains={"dev_chain": ["claude"]},
        agent_drains={"development": "dev_chain"},
        # custom_analysis drain intentionally absent everywhere — no inference
    )
    (config_dir / "pipeline.toml").write_text(
        dedent(
            """
            entry_phase = "development"
            terminal_phase = "complete"

            [phases.development]
            drain = "development"
            role = "execution"
            [phases.development.transitions]
            on_success = "custom_analysis"

            [phases.custom_analysis]
            drain = "custom_analysis"
            role = "execution"
            [phases.custom_analysis.transitions]
            on_success = "complete"

            [phases.complete]
            drain = "complete"
            role = "terminal"
            terminal_outcome = "success"
            [phases.complete.transitions]
            on_success = "complete"
            on_loopback = "complete"
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(LoaderPolicyValidationError, match="unbound drains"):
        load_policy(config_dir, config=config)


def test_drain_omitted_by_user_is_satisfied_by_bundled_default_binding(
    tmp_path: Path,
) -> None:
    """A pipeline drain the user omits but the bundled defaults bind loads
    cleanly — agents policy layers onto defaults like pipeline/artifacts."""
    config_dir = tmp_path / ".agent"
    config_dir.mkdir(parents=True)

    config = UnifiedConfig(
        agent_chains={"dev_chain": ["claude"]},
        agent_drains={"development": "dev_chain"},
        # development_analysis omitted: bound by the bundled default.
    )
    (config_dir / "pipeline.toml").write_text(
        dedent(
            """
            entry_phase = "development"
            terminal_phase = "complete"

            [phases.development]
            drain = "development"
            role = "execution"
            [phases.development.transitions]
            on_success = "development_analysis"

            [phases.development_analysis]
            drain = "development_analysis"
            role = "execution"
            [phases.development_analysis.transitions]
            on_success = "complete"

            [phases.complete]
            drain = "complete"
            role = "terminal"
            terminal_outcome = "success"
            [phases.complete.transitions]
            on_success = "complete"
            on_loopback = "complete"

            [recovery]
            failed_route = "complete"
            """
        ),
        encoding="utf-8",
    )

    bundle = load_policy(config_dir, config=config)

    assert bundle.agents.agent_drains["development"].chain == "dev_chain"
    assert bundle.agents.agent_drains["development_analysis"].chain == "development_analysis"


def test_terminal_recovery_route_rejected(tmp_path: Path) -> None:
    """Loading a pipeline.toml with the deprecated terminal_recovery_route field raises an error."""
    config_dir = tmp_path / ".agent"
    config_dir.mkdir(parents=True)

    config = UnifiedConfig(
        agent_chains={"main": ["claude"]},
        agent_drains={"planning": "main", "complete": "main"},
    )
    (config_dir / "pipeline.toml").write_text(
        dedent(
            """
            entry_phase = "planning"
            terminal_phase = "complete"

            [phases.planning]
            drain = "planning"
            role = "execution"
            [phases.planning.transitions]
            on_success = "complete"

            [phases.complete]
            drain = "complete"
            role = "terminal"
            terminal_outcome = "success"
            [phases.complete.transitions]
            on_success = "complete"
            on_loopback = "complete"

            [recovery]
            cycle_cap = 200
            terminal_recovery_route = "phase_failed"
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(LoaderPolicyValidationError, match="deprecated"):
        load_policy(config_dir, config=config)


def test_build_agents_policy_includes_custom_drains() -> None:
    """build_agents_policy_from_config includes all declared drains unconditionally."""

    config = UnifiedConfig(
        agent_chains={"custom_chain": ["claude"]},
        agent_drains={"my_custom_drain": "custom_chain"},
    )
    policy = build_agents_policy_from_config(config)
    assert "my_custom_drain" in policy.agent_drains
    assert policy.agent_drains["my_custom_drain"].chain == "custom_chain"


def test_default_policy_failed_analysis_decisions_route_to_same_rework_target() -> None:
    """Default policy must treat failed analysis as stronger rework, not termination."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"

    bundle = load_policy(defaults_dir)
    development_decisions = bundle.pipeline.phases["development_analysis"].decisions
    planning_decisions = bundle.pipeline.phases["planning_analysis"].decisions

    assert development_decisions is not None
    assert planning_decisions is not None
    assert development_decisions["failed"].target == development_decisions["request_changes"].target
    assert planning_decisions["failed"].target == planning_decisions["request_changes"].target
    assert development_decisions["failed"].target == "development"
    assert planning_decisions["failed"].target == "planning"


def test_default_policy_routes_planning_through_planning_analysis() -> None:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"

    bundle = load_policy(defaults_dir)
    planning = bundle.pipeline.phases["planning"]
    planning_analysis = bundle.pipeline.phases["planning_analysis"]

    assert planning.transitions.on_success == "planning_analysis"
    assert planning_analysis.role == "analysis"
    assert planning_analysis.transitions.on_success == "development"
    assert planning_analysis.transitions.on_loopback == "planning"
    assert planning_analysis.loop_policy is not None
    assert planning_analysis.loop_policy.iteration_state_field == "planning_analysis_iteration"
    assert (
        bundle.pipeline.loop_counters["planning_analysis_iteration"].default_max
        == PLANNING_ANALYSIS_DEFAULT_MAX_ITERATIONS
    )
    assert bundle.agents.agent_drains["planning_analysis"].drain_class == "analysis"
    contract = bundle.artifacts.artifacts["planning_analysis_decision"]
    assert contract.artifact_type == "planning_analysis_decision"
    assert contract.prompt_template == "planning_analysis.jinja"


def test_bundled_defaults_have_reviewless_phase_set() -> None:
    """Bundled default policy must expose the reviewless phase set and no reviewer_pass counter."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"

    bundle = load_policy(defaults_dir)
    expected_phases = {
        "planning",
        "planning_analysis",
        "development",
        "development_analysis",
        "development_commit_cleanup",
        "development_commit",
        "development_final_commit_cleanup",
        "development_final_commit",
        "complete",
        "failed_terminal",
    }
    assert set(bundle.pipeline.phases) == expected_phases
    assert "reviewer_pass" not in bundle.pipeline.budget_counters
    assert bundle.pipeline.entry_phase == "planning"
    assert bundle.pipeline.terminal_phase == "complete"

    # Bundled default agent surface must not expose review-era drains or chains.
    review_era_drains = {"review", "review_analysis", "review_commit", "fix"}
    assert not review_era_drains.intersection(bundle.agents.agent_drains), (
        f"Review-era drains still present in bundled defaults: "
        f"{review_era_drains.intersection(bundle.agents.agent_drains)}"
    )
    review_era_chains = {"review", "fix", "review_commit"}
    assert not review_era_chains.intersection(bundle.agents.agent_chains), (
        f"Review-era chains still present in bundled defaults: "
        f"{review_era_chains.intersection(bundle.agents.agent_chains)}"
    )


def test_default_policy_has_artifact_history_enabled_on_planning() -> None:
    """Default policy must have artifact_history.enabled=True on planning phase."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    planning = bundle.pipeline.phases["planning"]
    assert planning.artifact_history is not None, "planning phase must declare artifact_history"
    assert planning.artifact_history.enabled is True


def test_default_policy_has_artifact_history_enabled_on_planning_analysis() -> None:
    """Default policy must have artifact_history.enabled=True on planning_analysis phase."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    planning_analysis = bundle.pipeline.phases["planning_analysis"]
    assert planning_analysis.artifact_history is not None, (
        "planning_analysis phase must declare artifact_history"
    )
    assert planning_analysis.artifact_history.enabled is True


def test_default_policy_has_artifact_history_enabled_on_development() -> None:
    """Default policy must have artifact_history.enabled=True on development phase."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    development = bundle.pipeline.phases["development"]
    assert development.artifact_history is not None, (
        "development phase must declare artifact_history"
    )
    assert development.artifact_history.enabled is True


def test_default_policy_planning_clears_history_on_fresh_entry() -> None:
    """Default planning phase must clear history on fresh (non-loopback) entry."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    planning = bundle.pipeline.phases["planning"]
    assert planning.artifact_history is not None
    assert planning.artifact_history.clear_on_fresh_entry is True


def test_default_policy_planning_analysis_preserves_history_on_fresh_entry() -> None:
    """Default planning_analysis phase must NOT clear history on fresh entry."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    planning_analysis = bundle.pipeline.phases["planning_analysis"]
    assert planning_analysis.artifact_history is not None
    assert planning_analysis.artifact_history.clear_on_fresh_entry is False


def test_default_policy_development_clears_history_on_fresh_entry() -> None:
    """Default development phase must clear history on fresh (non-loopback) entry."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    development = bundle.pipeline.phases["development"]
    assert development.artifact_history is not None
    assert development.artifact_history.clear_on_fresh_entry is True
