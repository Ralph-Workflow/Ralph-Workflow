"""Unit tests for :mod:`ralph.policy.loader`."""

from __future__ import annotations

import shutil
from pathlib import Path
from textwrap import dedent
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.policy import loader as policy_loader
from ralph.policy.loader import (
    PolicyValidationError as LoaderPolicyValidationError,
)
from ralph.policy.loader import (
    build_agents_policy_from_config,
    format_validation_error_detail,
    format_validation_error_messages,
    format_validation_location,
    format_validation_message,
    load_policy,
    load_policy_for_workspace_scope,
    load_policy_or_die,
)
from ralph.policy.validation import PolicyValidationError as PolicyContractValidationError
from ralph.workspace.scope import WorkspaceScope

PLANNING_ANALYSIS_DEFAULT_MAX_ITERATIONS = 1
_GLOBAL_POLICY_MAX_PARALLEL_WORKERS = 3
_LEGACY_GLOBAL_POLICY_MAX_PARALLEL_WORKERS = 4


class _DummyValidationError:
    def __init__(self, errors: list[dict[str, object]]) -> None:
        self._errors = errors

    def errors(self) -> list[dict[str, object]]:
        return self._errors


def _copy_default_policy_files(target_dir: Path) -> None:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    target_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("agents.toml", "pipeline.toml", "artifacts.toml"):
        shutil.copy(defaults_dir / filename, target_dir / filename)


def test_format_validation_helpers_handle_various_inputs() -> None:
    detail: dict[str, object] = {"loc": ["agents", "chain"], "msg": "missing chain"}
    assert format_validation_error_detail(detail) == "  agents.chain: missing chain"
    assert format_validation_location(None) == "<root>"
    assert format_validation_location([]) == "<root>"
    assert format_validation_location("top") == "top"
    assert format_validation_message(None) == "<missing message>"
    assert format_validation_message(42) == "42"

    dummy = _DummyValidationError([detail, {"loc": None, "msg": "oops"}])
    messages = format_validation_error_messages(cast("Any", dummy))
    assert messages == [
        "  agents.chain: missing chain",
        "  <root>: oops",
    ]


def test_load_policy_invalid_toml_raises(tmp_path: Path) -> None:
    (tmp_path / "agents.toml").write_text("not a valid toml: <<<")
    with pytest.raises(LoaderPolicyValidationError) as excinfo:
        load_policy(tmp_path)
    assert "Failed to parse TOML" in excinfo.value.message
    assert excinfo.value.source == "agents.toml"


def test_load_policy_reports_agent_validation_failure(tmp_path: Path) -> None:
    (tmp_path / "agents.toml").write_text("[agent_chains.planning]\nagents = []\n")
    with pytest.raises(LoaderPolicyValidationError) as excinfo:
        load_policy(tmp_path)
    assert "agents.toml validation failed" in excinfo.value.message
    assert excinfo.value.source == "agents"


def test_load_policy_reports_unknown_transition_target(tmp_path: Path) -> None:
    _copy_default_policy_files(tmp_path)
    (tmp_path / "pipeline.toml").write_text(
        dedent(
            """
            [phases.planning]
            drain = "planning"
            prompt_template = "planning.jinja"
            [phases.planning.transitions]
            on_success = "missing_phase"

            [phases.complete]
            drain = "complete"
            [phases.complete.transitions]
            on_success = "complete"
            on_loopback = "complete"
            """
        )
    )

    with pytest.raises(LoaderPolicyValidationError) as excinfo:
        load_policy(tmp_path)
    assert "unknown phase 'missing_phase'" in excinfo.value.message
    assert excinfo.value.source == "pipeline"


def test_load_policy_reports_missing_entry_phase(tmp_path: Path) -> None:
    _copy_default_policy_files(tmp_path)
    (tmp_path / "pipeline.toml").write_text(
        dedent(
            """
            entry_phase = "planning"
            terminal_phase = "complete"

            [phases.complete]
            drain = "complete"
            [phases.complete.transitions]
            on_success = "complete"
            on_loopback = "complete"
            """
        )
    )

    with pytest.raises(LoaderPolicyValidationError) as excinfo:
        load_policy(tmp_path)
    assert "entry_phase 'planning' is not defined" in excinfo.value.message
    assert excinfo.value.source == "pipeline"


def test_load_policy_rejects_legacy_nested_pipeline_table(tmp_path: Path) -> None:
    _copy_default_policy_files(tmp_path)
    (tmp_path / "pipeline.toml").write_text(
        dedent(
            """
            [pipeline]
            entry_phase = "planning"
            terminal_phase = "complete"

            [pipeline.phases.planning]
            drain = "planning"
            role = "execution"
            prompt_template = "planning.jinja"
            [pipeline.phases.planning.transitions]
            on_success = "complete"

            [pipeline.phases.complete]
            drain = "complete"
            role = "terminal"
            terminal_outcome = "success"
            [pipeline.phases.complete.transitions]
            on_success = "complete"
            on_loopback = "complete"

            [pipeline.recovery]
            failed_route = "complete"
            """
        )
    )

    with pytest.raises(LoaderPolicyValidationError) as excinfo:
        load_policy(tmp_path)
    assert "obsolete [pipeline] wrapper format" in excinfo.value.message
    assert excinfo.value.source == "pipeline"


def test_load_policy_ignores_invalid_agents_toml_when_unified_config_is_provided(
    tmp_path: Path,
) -> None:
    _copy_default_policy_files(tmp_path)
    (tmp_path / "agents.toml").write_text("not valid toml: <<<", encoding="utf-8")
    config = UnifiedConfig(
        agent_chains={"planning": ["codex"], "complete": ["codex"]},
        agent_drains={"planning": "planning", "complete": "complete"},
    )
    (tmp_path / "pipeline.toml").write_text(
        dedent(
            """
            entry_phase = "planning"
            terminal_phase = "complete"

            [phases.planning]
            drain = "planning"
            role = "execution"
            prompt_template = "planning.jinja"
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
            failed_route = "complete"
            """
        ),
        encoding="utf-8",
    )

    bundle = load_policy(tmp_path, config=config)

    assert bundle.agents.agent_chains["planning"].agents == ["codex"]
    assert bundle.agents.agent_drains["planning"].chain == "planning"


def test_load_policy_synthesizes_drain_class_from_unified_config_builtin_drains() -> None:
    config = UnifiedConfig(
        agent_chains={
            "planning": ["codex"],
            "development": ["codex"],
            "analysis": ["codex"],
            "commit": ["codex"],
        },
        agent_drains={
            "planning": "planning",
            "development": "development",
            "development_analysis": "analysis",
            "development_commit": "commit",
        },
    )

    agents_policy = build_agents_policy_from_config(config)

    assert agents_policy.agent_drains["planning"].drain_class == "planning"
    assert agents_policy.agent_drains["development"].drain_class == "development"
    assert agents_policy.agent_drains["development_analysis"].drain_class == "analysis"
    assert agents_policy.agent_drains["development_commit"].drain_class == "commit"


def test_load_policy_uses_unified_config_for_agents_policy_when_provided(tmp_path: Path) -> None:
    _copy_default_policy_files(tmp_path)
    config = UnifiedConfig(
        agent_chains={"planning": ["codex"]},
        agent_drains={"planning": "planning"},
    )
    (tmp_path / "pipeline.toml").write_text(
        dedent(
            """
            entry_phase = "planning"
            terminal_phase = "complete"

            [phases.planning]
            drain = "planning"
            role = "execution"
            prompt_template = "planning.jinja"
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
            failed_route = "complete"
            """
        ),
        encoding="utf-8",
    )

    bundle = load_policy(tmp_path, config=config)

    assert bundle.agents.agent_chains["planning"].agents == ["codex"]
    assert bundle.agents.agent_drains["planning"].chain == "planning"


def test_load_policy_rejects_artifact_required_in_artifacts_toml(tmp_path: Path) -> None:
    _copy_default_policy_files(tmp_path)
    (tmp_path / "artifacts.toml").write_text(
        dedent(
            """
            [artifacts.planning_output]
            drain = "planning"
            artifact_type = "plan"
            decision_vocabulary = []
            prompt_template = "planning.jinja"
            markdown_summary_path = ".agent/PLAN.md"

            [artifacts.development_output]
            drain = "development"
            artifact_type = "development_result"
            artifact_required = false
            decision_vocabulary = []
            prompt_template = "developer_iteration.jinja"
            markdown_summary_path = ".agent/DEVELOPMENT_RESULT.md"
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(LoaderPolicyValidationError, match="artifact_required"):
        load_policy(tmp_path)


def test_default_pipeline_toml_makes_development_artifact_required(
    tmp_path: Path,
) -> None:
    _copy_default_policy_files(tmp_path)

    bundle = load_policy(tmp_path)

    assert bundle.pipeline.phases["development"].artifact_required is True


def test_load_policy_wraps_validate_drain_contracts_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "policy"
    _copy_default_policy_files(config_dir)

    def fake_validate(_: object) -> None:
        raise PolicyContractValidationError("drain contract failure")

    monkeypatch.setattr(policy_loader, "validate_drain_contracts", fake_validate)

    with pytest.raises(LoaderPolicyValidationError) as excinfo:
        load_policy(config_dir)
    assert excinfo.value.message == "drain contract failure"
    assert excinfo.value.source == "agents"


def test_load_policy_for_workspace_scope_uses_global_policy_when_local_override_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    global_dir = tmp_path / "xdg"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    (global_dir / "ralph-workflow-pipeline.toml").write_text(
        (defaults_dir / "pipeline.toml")
        .read_text(encoding="utf-8")
        .replace(
            "max_parallel_workers = 8",
            f"max_parallel_workers = {_GLOBAL_POLICY_MAX_PARALLEL_WORKERS}",
        ),
        encoding="utf-8",
    )
    (global_dir / "ralph-workflow-artifacts.toml").write_text(
        (defaults_dir / "artifacts.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    bundle = load_policy_for_workspace_scope(WorkspaceScope(workspace_root))

    assert bundle.pipeline.phases["development"].parallelization is not None
    assert (
        bundle.pipeline.phases["development"].parallelization.max_parallel_workers
        == _GLOBAL_POLICY_MAX_PARALLEL_WORKERS
    )


def test_load_policy_for_workspace_scope_rejects_phase_authored_global_override_before_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    global_dir = tmp_path / "xdg"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    (global_dir / "ralph-workflow-pipeline.toml").write_text(
        dedent(
            """
            entry_phase = "planning"
            terminal_phase = "complete"

            [loop_counters.development_analysis_iteration]
            default_max = 10
            description = "Development analysis loop iteration counter"

            [loop_counters.planning_analysis_iteration]
            default_max = 2
            description = "Planning analysis loop iteration counter"

            [budget_counters.iteration]
            description = "Development iteration counter (developer cycles)"
            tracks_budget = true
            default_max = 5

            [phases.planning]
            drain = "planning"
            role = "execution"
            prompt_template = "planning.jinja"
            loopback_prompt_template = "planning_edit.jinja"
            [phases.planning.transitions]
            on_success = "planning_analysis"

            [phases.planning_analysis]
            drain = "planning_analysis"
            role = "analysis"
            prompt_template = "planning_analysis.jinja"
            [phases.planning_analysis.transitions]
            on_success = "development"
            on_loopback = "planning"
            [phases.planning_analysis.loop_policy]
            iteration_state_field = "planning_analysis_iteration"
            [phases.planning_analysis.decisions.completed]
            target = "development"
            reset_loop = true
            [phases.planning_analysis.decisions.request_changes]
            target = "planning"
            reset_loop = false
            [phases.planning_analysis.decisions.failed]
            target = "planning"
            reset_loop = false

            [phases.development]
            drain = "development"
            role = "execution"
            prompt_template = "developer_iteration.jinja"
            continuation_template = "developer_iteration_continuation.jinja"
            [phases.development.transitions]
            on_success = "development_analysis"
            on_loopback = "development"

            [phases.development.parallelization]
            mode = "same_workspace"
            max_parallel_workers = 8
            max_work_units = 50
            require_allowed_directories = true
            post_fanout_verification = false

            [phases.development_analysis]
            drain = "development_analysis"
            role = "analysis"
            prompt_template = "development_analysis.jinja"
            [phases.development_analysis.transitions]
            on_success = "development_commit"
            on_loopback = "development"
            [phases.development_analysis.loop_policy]
            iteration_state_field = "development_analysis_iteration"
            [phases.development_analysis.decisions.completed]
            target = "development_commit"
            reset_loop = true
            [phases.development_analysis.decisions.request_changes]
            target = "development"
            reset_loop = false
            [phases.development_analysis.decisions.failed]
            target = "development"
            reset_loop = false

            [phases.development_commit]
            drain = "development_commit"
            role = "commit"
            prompt_template = "commit_message.jinja"
            [phases.development_commit.transitions]
            on_success = "complete"
            on_failure = "failed_terminal"
            [phases.development_commit.commit_policy]
            requires_artifact = true
            skipped_advances_progress = true
            increments_counter = "iteration"
            loop_resets = ["development_analysis_iteration"]

            [phases.complete]
            drain = "complete"
            role = "terminal"
            terminal_outcome = "success"
            [phases.complete.transitions]
            on_success = "complete"
            on_loopback = "complete"

            [phases.failed_terminal]
            drain = "failed_terminal"
            role = "terminal"
            terminal_outcome = "failure"
            [phases.failed_terminal.transitions]
            on_success = "failed_terminal"
            on_loopback = "failed_terminal"

            [[post_commit_routes]]
            target = "planning"
            [post_commit_routes.when]
            phase = "development_commit"
            budget_state = "remaining"

            [[post_commit_routes]]
            target = "complete"
            [post_commit_routes.when]
            phase = "development_commit"
            budget_state = "exhausted"

            [[post_commit_routes]]
            target = "complete"
            [post_commit_routes.when]
            phase = "development_commit"
            budget_state = "no_review"

            [default_phase_retry_policy]
            max_retries = 3
            retry_delay_ms = 1000
            retry_in_session = false

            [recovery]
            cycle_cap = 200
            failed_route = "failed_terminal"
            terminal_failure_phase = "failed_terminal"
            preserve_session_on_categories = ["agent"]
            """
        ).strip(),
        encoding="utf-8",
    )
    (global_dir / "ralph-workflow-artifacts.toml").write_text(
        (defaults_dir / "artifacts.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(LoaderPolicyValidationError) as excinfo:
        load_policy_for_workspace_scope(WorkspaceScope(workspace_root))

    assert "phase-authored" in excinfo.value.message
    assert "must not be merged" in excinfo.value.message
    assert "Remove the outdated file" in excinfo.value.message
    assert "ralph-workflow-pipeline.toml" in excinfo.value.message
    assert excinfo.value.source == "pipeline"


def test_load_policy_for_workspace_scope_ignores_legacy_global_policy_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    global_dir = tmp_path / "xdg"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    (global_dir / "pipeline.toml").write_text(
        (defaults_dir / "pipeline.toml")
        .read_text(encoding="utf-8")
        .replace(
            "max_parallel_workers = 8",
            f"max_parallel_workers = {_LEGACY_GLOBAL_POLICY_MAX_PARALLEL_WORKERS}",
        ),
        encoding="utf-8",
    )
    (global_dir / "artifacts.toml").write_text(
        (defaults_dir / "artifacts.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    bundle = load_policy_for_workspace_scope(WorkspaceScope(workspace_root))

    assert bundle.pipeline.phases["development"].parallelization is not None
    assert (
        bundle.pipeline.phases["development"].parallelization.max_parallel_workers
        != _LEGACY_GLOBAL_POLICY_MAX_PARALLEL_WORKERS
    )
    assert bundle.pipeline.entry_block == "developer_iteration"


def test_load_policy_or_die_exits_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_load(_: Path) -> None:
        raise LoaderPolicyValidationError("boom", source="agents")

    mock_logger = MagicMock()
    monkeypatch.setattr(policy_loader, "load_policy", fake_load)
    monkeypatch.setattr(policy_loader, "logger", mock_logger)

    with pytest.raises(SystemExit) as excinfo:
        load_policy_or_die(Path("unused"))
    assert excinfo.value.code == 1

    expected_messages: list[tuple[str, str]] = [
        ("Policy validation failed: {}", "boom"),
        ("  Source: {}", "agents"),
    ]
    assert mock_logger.error.call_count == len(expected_messages)
    for idx, (fmt, value) in enumerate(expected_messages):
        assert mock_logger.error.call_args_list[idx][0][0] == fmt
        assert mock_logger.error.call_args_list[idx][0][1] == value


def test_build_agents_policy_from_config_rejects_missing_drain(tmp_path: Path) -> None:
    """After removing sibling-drain inference, a pipeline drain missing from
    agent_drains must cause a cross-policy validation failure at load time.
    """
    config_dir = tmp_path / ".agent"
    config_dir.mkdir(parents=True)

    config = UnifiedConfig(
        agent_chains={"dev_chain": ["claude"]},
        agent_drains={"development": "dev_chain"},
        # development_analysis drain intentionally absent — no sibling inference
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
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(LoaderPolicyValidationError, match="unbound drains"):
        load_policy(config_dir, config=config)


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
