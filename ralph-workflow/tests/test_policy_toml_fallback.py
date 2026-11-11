from __future__ import annotations

import stat
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.config import reset_fallback_warning_registry
from ralph.policy.loader import (
    PolicyValidationError,
    cached_default_agents_policy,
    cached_default_artifacts_policy,
    cached_default_pipeline_policy,
    default_dir,
    load_agents_policy,
    load_policy,
    load_policy_for_config_dir,
    load_policy_for_workspace_scope,
    load_toml,
    validate_artifacts,
    validate_pipeline,
)
from ralph.project_policy.portfolio import (
    PortfolioError,
    default_portfolio,
    load_portfolio_toml,
    parse_portfolio_toml,
)
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator


@pytest.fixture(autouse=True)
def _clear_fallback_registry() -> Generator[None, None, None]:
    reset_fallback_warning_registry()
    try:
        yield
    finally:
        reset_fallback_warning_registry()


@contextmanager
def _capture_warnings() -> Iterator[list[str]]:
    records: list[str] = []

    def sink(message: str) -> None:
        if "using built-in default" in message:
            records.append(message)

    sink_id = logger.add(sink, level="WARNING", format="{message}")
    try:
        yield records
    finally:
        logger.remove(sink_id)


def _assert_fallback_warning(
    record: str,
    path: Path,
    *,
    expected_kind: str,
) -> None:
    assert str(path) in record
    assert expected_kind in record.lower()
    assert "using built-in default" in record
    assert "ralph --check-config" in record


def test_pipeline_toml_syntax_error_fallback(tmp_path: Path) -> None:
    pipeline_file = tmp_path / "pipeline.toml"
    bad_content = b"phases = [\nunclosed syntax"
    pipeline_file.write_bytes(bad_content)

    with _capture_warnings() as warnings:
        bundle = load_policy(tmp_path)

    default_pipeline = validate_pipeline(
        load_toml(default_dir() / "pipeline.toml", kind="pipeline", strict=True),
        timeboxes_explicit=False,
    )
    assert bundle.pipeline.entry_block == default_pipeline.entry_block
    assert bundle.pipeline.phases.keys() == default_pipeline.phases.keys()
    assert pipeline_file.read_bytes() == bad_content
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], pipeline_file, expected_kind="pipeline")


def test_pipeline_toml_schema_invalid_fallback(tmp_path: Path) -> None:
    pipeline_file = tmp_path / "pipeline.toml"
    bad_schema = dedent(
        """
        [phases.planning]
        drain = "planning"
        prompt_template = "planning.jinja"
        [phases.planning.transitions]
        on_success = "non_existent_phase"

        [phases.complete]
        drain = "complete"
        [phases.complete.transitions]
        on_success = "complete"
        on_loopback = "complete"
        """
    )
    pipeline_file.write_text(bad_schema, encoding="utf-8")
    before_bytes = pipeline_file.read_bytes()

    with _capture_warnings() as warnings:
        bundle = load_policy(tmp_path)

    default_pipeline = validate_pipeline(
        load_toml(default_dir() / "pipeline.toml", kind="pipeline", strict=True),
        timeboxes_explicit=False,
    )
    assert bundle.pipeline.entry_block == default_pipeline.entry_block
    assert pipeline_file.read_bytes() == before_bytes
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], pipeline_file, expected_kind="pipeline")


def test_pipeline_toml_strict_mode_raises(tmp_path: Path) -> None:
    pipeline_file = tmp_path / "pipeline.toml"
    pipeline_file.write_text("broken = [syntax", encoding="utf-8")

    with pytest.raises(PolicyValidationError):
        load_policy(tmp_path, strict_optional_files=True)


def test_agents_toml_syntax_error_fallback(tmp_path: Path) -> None:
    agents_file = tmp_path / "agents.toml"
    bad_content = b"agents = [\nnot closed"
    agents_file.write_bytes(bad_content)

    with _capture_warnings() as warnings:
        bundle = load_policy(tmp_path)

    default_agents = cached_default_agents_policy()
    assert bundle.agents.agent_chains.keys() == default_agents.agent_chains.keys()
    assert bundle.agents.agent_drains.keys() == default_agents.agent_drains.keys()
    assert agents_file.read_bytes() == bad_content
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], agents_file, expected_kind="agents")


def test_agents_toml_schema_invalid_fallback(tmp_path: Path) -> None:
    agents_file = tmp_path / "agents.toml"
    bad_schema = "[agent_chains.planning]\nagents = []\n"
    agents_file.write_text(bad_schema, encoding="utf-8")
    before_bytes = agents_file.read_bytes()

    with _capture_warnings() as warnings:
        policy = load_agents_policy(tmp_path)

    default_agents = cached_default_agents_policy()
    assert policy.agent_chains.keys() == default_agents.agent_chains.keys()
    assert agents_file.read_bytes() == before_bytes
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], agents_file, expected_kind="agents")


def test_agents_toml_strict_mode_raises(tmp_path: Path) -> None:
    agents_file = tmp_path / "agents.toml"
    agents_file.write_text("[agent_chains.planning]\nagents = []\n", encoding="utf-8")

    with pytest.raises(PolicyValidationError):
        load_agents_policy(tmp_path, strict_optional_files=True)


def test_artifacts_toml_syntax_error_fallback(tmp_path: Path) -> None:
    artifacts_file = tmp_path / "artifacts.toml"
    bad_content = b"artifacts = { broken"
    artifacts_file.write_bytes(bad_content)

    with _capture_warnings() as warnings:
        bundle = load_policy(tmp_path)

    default_artifacts = validate_artifacts(
        load_toml(default_dir() / "artifacts.toml", kind="artifacts", strict=True)
    )
    assert bundle.artifacts.artifacts.keys() == default_artifacts.artifacts.keys()
    assert artifacts_file.read_bytes() == bad_content
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], artifacts_file, expected_kind="artifacts")


def test_artifacts_toml_schema_invalid_fallback(tmp_path: Path) -> None:
    artifacts_file = tmp_path / "artifacts.toml"
    bad_schema = dedent(
        """
        [artifacts.plan]
        artifact_required = "not-a-bool"
        path = "PLAN.md"
        """
    )
    artifacts_file.write_text(bad_schema, encoding="utf-8")
    before_bytes = artifacts_file.read_bytes()

    with _capture_warnings() as warnings:
        bundle = load_policy(tmp_path)

    default_artifacts = validate_artifacts(
        load_toml(default_dir() / "artifacts.toml", kind="artifacts", strict=True)
    )
    assert bundle.artifacts.artifacts.keys() == default_artifacts.artifacts.keys()
    assert artifacts_file.read_bytes() == before_bytes
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], artifacts_file, expected_kind="artifacts")


def test_artifacts_toml_strict_mode_raises(tmp_path: Path) -> None:
    artifacts_file = tmp_path / "artifacts.toml"
    artifacts_file.write_text("invalid = {", encoding="utf-8")

    with pytest.raises(PolicyValidationError):
        load_policy(tmp_path, strict_optional_files=True)


def test_other_user_policy_toml_fallback(tmp_path: Path) -> None:
    custom_policy_file = tmp_path / "custom-policy.toml"
    bad_content = b"custom = [unclosed"
    custom_policy_file.write_bytes(bad_content)

    with _capture_warnings() as warnings:
        data = load_toml(custom_policy_file, kind="policy", strict=False)

    assert data == {}
    assert custom_policy_file.read_bytes() == bad_content
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], custom_policy_file, expected_kind="policy")


def test_other_user_policy_toml_permission_error_fallback(tmp_path: Path) -> None:
    policy_file = tmp_path / "unreadable-policy.toml"
    policy_file.write_text("key = 'value'\n", encoding="utf-8")
    before_bytes = policy_file.read_bytes()
    policy_file.chmod(0o000)

    try:
        with _capture_warnings() as warnings:
            data = load_toml(policy_file, kind="policy", strict=False)
        assert data == {}
        assert len(warnings) == 1
        _assert_fallback_warning(warnings[0], policy_file, expected_kind="policy")
    finally:
        policy_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert policy_file.read_bytes() == before_bytes


def test_other_user_policy_toml_bad_encoding_fallback(tmp_path: Path) -> None:
    bad_encoding_file = tmp_path / "bad-encoding.toml"
    bad_bytes = b"\xff\xfe\x00\x01\x02"
    bad_encoding_file.write_bytes(bad_bytes)

    with _capture_warnings() as warnings:
        data = load_toml(bad_encoding_file, kind="policy", strict=False)

    assert data == {}
    assert bad_encoding_file.read_bytes() == bad_bytes
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], bad_encoding_file, expected_kind="policy")


def test_user_portfolio_toml_syntax_error_fallback(tmp_path: Path) -> None:
    portfolio_file = tmp_path / "policy-portfolio.toml"
    bad_content = b"schema_version = 'v3'\n[controls\nbad syntax"
    portfolio_file.write_bytes(bad_content)

    with _capture_warnings() as warnings:
        portfolio = load_portfolio_toml(portfolio_file)

    expected_default = default_portfolio()
    assert portfolio.schema_version == expected_default.schema_version
    assert portfolio.default_budget_seconds == expected_default.default_budget_seconds
    assert portfolio_file.read_bytes() == bad_content
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], portfolio_file, expected_kind="portfolio")


def test_user_portfolio_toml_schema_invalid_fallback(tmp_path: Path) -> None:
    portfolio_file = tmp_path / "policy-portfolio.toml"
    bad_schema = dedent(
        """
        schema_version = "invalid_version"
        default_budget_seconds = 100.0
        """
    )
    portfolio_file.write_text(bad_schema, encoding="utf-8")
    before_bytes = portfolio_file.read_bytes()

    with _capture_warnings() as warnings:
        portfolio = load_portfolio_toml(portfolio_file)

    expected_default = default_portfolio()
    assert portfolio.schema_version == expected_default.schema_version
    assert portfolio_file.read_bytes() == before_bytes
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], portfolio_file, expected_kind="portfolio")


def test_user_portfolio_toml_missing_file_silent_default(tmp_path: Path) -> None:
    missing_file = tmp_path / "does_not_exist.toml"

    with _capture_warnings() as warnings:
        portfolio = load_portfolio_toml(missing_file)

    expected_default = default_portfolio()
    assert portfolio.schema_version == expected_default.schema_version
    assert warnings == []


def test_user_portfolio_toml_strict_mode_raises(tmp_path: Path) -> None:
    portfolio_file = tmp_path / "policy-portfolio.toml"
    portfolio_file.write_text("schema_version = 'invalid'\n", encoding="utf-8")

    with pytest.raises(PortfolioError):
        load_portfolio_toml(portfolio_file, strict_optional_files=True)


def test_user_portfolio_toml_second_read_error_warns_and_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    portfolio_file = tmp_path / "policy-portfolio.toml"
    portfolio_file.write_text("schema_version = 'v3'\n", encoding="utf-8")

    orig_read_text = Path.read_text

    def _failing_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == portfolio_file:
            raise OSError("simulated second-read failure")
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _failing_read_text)

    with _capture_warnings() as warnings:
        portfolio = load_portfolio_toml(portfolio_file)

    expected_default = default_portfolio()
    assert portfolio.schema_version == expected_default.schema_version
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], portfolio_file, expected_kind="portfolio")


def test_parse_portfolio_toml_fallback_mode() -> None:
    with _capture_warnings() as warnings:
        portfolio = parse_portfolio_toml(
            "schema_version = 'invalid'\n",
            path="custom/path/policy-portfolio.toml",
            strict_optional_files=False,
        )

    expected_default = default_portfolio()
    assert portfolio.schema_version == expected_default.schema_version
    assert len(warnings) == 1
    _assert_fallback_warning(
        warnings[0],
        Path("custom/path/policy-portfolio.toml"),
        expected_kind="portfolio",
    )


def test_fallback_warning_deduplication(tmp_path: Path) -> None:
    agents_file = tmp_path / "agents.toml"
    agents_file.write_text("broken = <<<", encoding="utf-8")

    with _capture_warnings() as warnings:
        load_policy(tmp_path)
        load_policy(tmp_path)
        load_agents_policy(tmp_path)

    assert len(warnings) == 1


def test_fallback_rewarns_when_content_changes(tmp_path: Path) -> None:
    pipeline_file = tmp_path / "pipeline.toml"
    pipeline_file.write_text("broken_1 = <<<", encoding="utf-8")

    with _capture_warnings() as warnings_1:
        load_policy(tmp_path)
    assert len(warnings_1) == 1

    pipeline_file.write_text("broken_2 = >>>", encoding="utf-8")
    with _capture_warnings() as warnings_2:
        load_policy(tmp_path)
    assert len(warnings_2) == 1


def test_global_pipeline_toml_schema_invalid_fallback_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    global_file = global_dir / "ralph-workflow-pipeline.toml"
    bad_content = "entry_block = 42\n"
    global_file.write_text(bad_content, encoding="utf-8")
    before_bytes = global_file.read_bytes()

    local_dir = tmp_path / "workspace" / ".agent"
    local_dir.mkdir(parents=True)
    local_file = local_dir / "pipeline.toml"

    with _capture_warnings() as warnings:
        bundle = load_policy_for_config_dir(local_dir)

    default_pipeline = cached_default_pipeline_policy()
    assert bundle.pipeline.entry_block == default_pipeline.entry_block
    assert global_file.read_bytes() == before_bytes
    assert not local_file.exists()
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], global_file, expected_kind="pipeline")
    assert str(local_file) not in warnings[0]

    with _capture_warnings() as warnings_repeat:
        load_policy_for_config_dir(local_dir)
    assert len(warnings_repeat) == 0

    global_file.write_text("entry_block = [1, 2]\n", encoding="utf-8")
    with _capture_warnings() as warnings_changed:
        load_policy_for_config_dir(local_dir)
    assert len(warnings_changed) == 1
    _assert_fallback_warning(warnings_changed[0], global_file, expected_kind="pipeline")


def test_global_artifacts_toml_schema_invalid_fallback_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    global_file = global_dir / "ralph-workflow-artifacts.toml"
    bad_content = "[artifacts.plan]\nartifact_required = 'not-a-bool'\n"
    global_file.write_text(bad_content, encoding="utf-8")
    before_bytes = global_file.read_bytes()

    local_dir = tmp_path / "workspace" / ".agent"
    local_dir.mkdir(parents=True)
    local_file = local_dir / "artifacts.toml"

    with _capture_warnings() as warnings:
        bundle = load_policy_for_config_dir(local_dir)

    default_artifacts = cached_default_artifacts_policy()
    assert bundle.artifacts.artifacts.keys() == default_artifacts.artifacts.keys()
    assert global_file.read_bytes() == before_bytes
    assert not local_file.exists()
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], global_file, expected_kind="artifacts")
    assert str(local_file) not in warnings[0]

    with _capture_warnings() as warnings_repeat:
        load_policy_for_config_dir(local_dir)
    assert len(warnings_repeat) == 0

    global_file.write_text("[artifacts.plan]\nartifact_required = 12345\n", encoding="utf-8")
    with _capture_warnings() as warnings_changed:
        load_policy_for_config_dir(local_dir)
    assert len(warnings_changed) == 1
    _assert_fallback_warning(warnings_changed[0], global_file, expected_kind="artifacts")


def test_global_pipeline_fallback_preserves_valid_local_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    global_file = global_dir / "ralph-workflow-pipeline.toml"
    global_file.write_text("entry_block = 42\n", encoding="utf-8")

    local_dir = tmp_path / "workspace" / ".agent"
    local_dir.mkdir(parents=True)
    local_file = local_dir / "pipeline.toml"
    local_file.write_text(
        "[development_timebox]\nduration_seconds = 7200\nwarning_seconds = 6000\n",
        encoding="utf-8",
    )

    with _capture_warnings() as warnings:
        bundle = load_policy_for_config_dir(local_dir)

    assert bundle.pipeline.development_timebox is not None
    assert bundle.pipeline.development_timebox.duration_seconds == 7200
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], global_file, expected_kind="pipeline")
    assert str(local_file) not in warnings[0]


def test_local_pipeline_fallback_preserves_valid_global_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    global_file = global_dir / "ralph-workflow-pipeline.toml"
    global_file.write_text(
        "[development_timebox]\nduration_seconds = 7200\nwarning_seconds = 6000\n",
        encoding="utf-8",
    )

    local_dir = tmp_path / "workspace" / ".agent"
    local_dir.mkdir(parents=True)
    local_file = local_dir / "pipeline.toml"
    local_file.write_text("entry_block = 42\n", encoding="utf-8")

    with _capture_warnings() as warnings:
        bundle = load_policy_for_config_dir(local_dir)

    assert bundle.pipeline.development_timebox is not None
    assert bundle.pipeline.development_timebox.duration_seconds == 7200
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], local_file, expected_kind="pipeline")
    assert str(global_file) not in warnings[0]


def test_global_artifacts_fallback_preserves_valid_local_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    global_file = global_dir / "ralph-workflow-artifacts.toml"
    global_file.write_text(
        "[artifacts.planning_output]\nartifact_required = 'not-a-bool'\n", encoding="utf-8"
    )

    local_dir = tmp_path / "workspace" / ".agent"
    local_dir.mkdir(parents=True)
    local_file = local_dir / "artifacts.toml"
    local_file.write_text(
        "[artifacts.planning_output]\nmarkdown_summary_path = '.agent/CUSTOM_PLAN.md'\n",
        encoding="utf-8",
    )

    with _capture_warnings() as warnings:
        bundle = load_policy_for_config_dir(local_dir)

    assert (
        bundle.artifacts.artifacts["planning_output"].markdown_summary_path
        == ".agent/CUSTOM_PLAN.md"
    )
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], global_file, expected_kind="artifacts")
    assert str(local_file) not in warnings[0]


def test_local_artifacts_fallback_preserves_valid_global_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    global_file = global_dir / "ralph-workflow-artifacts.toml"
    global_file.write_text(
        "[artifacts.planning_output]\nmarkdown_summary_path = '.agent/GLOBAL_PLAN.md'\n",
        encoding="utf-8",
    )

    local_dir = tmp_path / "workspace" / ".agent"
    local_dir.mkdir(parents=True)
    local_file = local_dir / "artifacts.toml"
    local_file.write_text(
        "[artifacts.planning_output]\nartifact_required = 'not-a-bool'\n", encoding="utf-8"
    )

    with _capture_warnings() as warnings:
        bundle = load_policy_for_config_dir(local_dir)

    assert (
        bundle.artifacts.artifacts["planning_output"].markdown_summary_path
        == ".agent/GLOBAL_PLAN.md"
    )
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], local_file, expected_kind="artifacts")
    assert str(global_file) not in warnings[0]


def test_workspace_scope_global_fallback_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_dir))
    global_file = global_dir / "ralph-workflow-pipeline.toml"
    global_file.write_text("entry_block = 42\n", encoding="utf-8")

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    local_agent_dir = workspace_root / ".agent"
    local_agent_dir.mkdir()

    with _capture_warnings() as warnings:
        bundle = load_policy_for_workspace_scope(WorkspaceScope(workspace_root))

    default_pipeline = cached_default_pipeline_policy()
    assert bundle.pipeline.entry_block == default_pipeline.entry_block
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], global_file, expected_kind="pipeline")
    assert str(local_agent_dir) not in warnings[0]
