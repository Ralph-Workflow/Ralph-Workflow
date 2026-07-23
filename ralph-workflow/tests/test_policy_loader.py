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


def test_config_synthesized_agents_policy_backfills_policy_remediation(
    tmp_path: Path,
) -> None:
    """A user config predating the policy_remediation chain must not block runs.

    When the unified config defines its own agent_chains/agent_drains, the
    out-of-graph policy_remediation chain is backfilled from the bundled
    defaults instead of silently disappearing, and the drain aliases to the
    chain behind the user's development drain when one exists.
    """
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

    agents_policy = policy_loader.load_agents_policy(tmp_path, config=config)

    remediation_chain = agents_policy.agent_chains.get("policy_remediation")
    assert remediation_chain is not None
    assert remediation_chain.agents == ["claude"]
    remediation_drain = agents_policy.agent_drains.get("policy_remediation")
    assert remediation_drain is not None
    assert remediation_drain.chain == "development"
    assert agents_policy.agent_chains["planning"].agents == ["codex"]


def test_project_agents_toml_backfills_policy_remediation(tmp_path: Path) -> None:
    (tmp_path / "agents.toml").write_text(
        dedent(
            """
            [agent_chains.planning]
            agents = ["codex"]

            [agent_drains.planning]
            chain = "planning"
            drain_class = "planning"
            """
        ),
        encoding="utf-8",
    )

    agents_policy = policy_loader.load_agents_policy(tmp_path)

    remediation_chain = agents_policy.agent_chains.get("policy_remediation")
    assert remediation_chain is not None
    assert remediation_chain.agents == ["claude"]
    assert agents_policy.agent_drains["policy_remediation"].chain == "policy_remediation"


def test_project_agents_toml_merges_onto_defaults_like_other_policies(
    tmp_path: Path,
) -> None:
    """agents.toml layers onto bundled defaults exactly like pipeline.toml
    and artifacts.toml: user entries win per name, everything else keeps
    its bundled default."""
    (tmp_path / "agents.toml").write_text(
        dedent(
            """
            [agent_chains.development]
            agents = ["custom-dev"]

            [agent_drains.development]
            chain = "development"
            drain_class = "development"
            """
        ),
        encoding="utf-8",
    )

    agents_policy = policy_loader.load_agents_policy(tmp_path)

    assert agents_policy.agent_chains["development"].agents == ["custom-dev"]
    assert agents_policy.agent_chains["planning"].agents == ["claude"]
    assert agents_policy.agent_drains["commit"].chain == "commit"


def test_config_synthesized_policy_keeps_default_chains_not_overridden(
    tmp_path: Path,
) -> None:
    config = UnifiedConfig(
        agent_chains={"planning": ["codex"]},
        agent_drains={"planning": "planning"},
    )

    agents_policy = policy_loader.load_agents_policy(tmp_path, config=config)

    assert agents_policy.agent_chains["planning"].agents == ["codex"]
    assert agents_policy.agent_chains["development"].agents == ["claude"]
    assert agents_policy.agent_drains["development"].chain == "development"


def test_backfilled_policy_remediation_binds_development_drain_chain(
    tmp_path: Path,
) -> None:
    """When the user policy binds a development drain, policy remediation
    routes through the same chain instead of the bundled default. The shipped
    pipeline has no review drain, so the development chain is the alias
    target."""
    config = UnifiedConfig(
        agent_chains={
            "planning": ["codex"],
            "development": ["dev-agent", "codex"],
        },
        agent_drains={
            "planning": "planning",
            "development": "development",
        },
    )

    agents_policy = policy_loader.load_agents_policy(tmp_path, config=config)

    remediation_drain = agents_policy.agent_drains.get("policy_remediation")
    assert remediation_drain is not None
    assert remediation_drain.chain == "development"
    assert agents_policy.agent_chains["development"].agents == ["dev-agent", "codex"]


def test_backfilled_policy_remediation_ignores_review_drain(tmp_path: Path) -> None:
    """A legacy review drain must NOT capture policy remediation: the shipped
    pipeline has no review drain, so aliasing to it routes remediation into a
    chain the pipeline never runs. Without a development drain the bundled
    default binding survives."""
    config = UnifiedConfig(
        agent_chains={
            "planning": ["codex"],
            "review": ["reviewer-agent", "codex"],
        },
        agent_drains={
            "planning": "planning",
            "review": "review",
        },
    )

    agents_policy = policy_loader.load_agents_policy(tmp_path, config=config)

    remediation_drain = agents_policy.agent_drains.get("policy_remediation")
    assert remediation_drain is not None
    assert remediation_drain.chain == "policy_remediation"


def test_user_defined_policy_remediation_chain_is_not_overwritten(tmp_path: Path) -> None:
    config = UnifiedConfig(
        agent_chains={
            "planning": ["codex"],
            "policy_remediation": ["opencode"],
        },
        agent_drains={
            "planning": "planning",
            "policy_remediation": "policy_remediation",
        },
    )

    agents_policy = policy_loader.load_agents_policy(tmp_path, config=config)

    assert agents_policy.agent_chains["policy_remediation"].agents == ["opencode"]


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


