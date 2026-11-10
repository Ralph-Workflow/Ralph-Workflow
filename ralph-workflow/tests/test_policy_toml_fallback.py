"""Tests for policy TOML fallback resilience across all policy kinds.

Verifies that invalid TOML syntax, invalid encodings, permission errors,
and schema validation failures in optional policy files fall back to
built-in defaults, leave file bytes on disk untouched, and emit compliant
loguru warnings mentioning the file, parse error, default-in-use, and
fix hint (ralph --check-config).
"""

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
    default_dir,
    load_agents_policy,
    load_policy,
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

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator


@pytest.fixture(autouse=True)
def _clear_fallback_registry() -> Generator[None, None, None]:
    """Clear warning registry before and after every test."""
    reset_fallback_warning_registry()
    try:
        yield
    finally:
        reset_fallback_warning_registry()


@contextmanager
def _capture_warnings() -> Iterator[list[str]]:
    """Capture loguru fallback warning messages."""
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
    """Assert that a loguru warning matches the required fallback format."""
    assert str(path) in record
    assert expected_kind in record.lower()
    assert "using built-in default" in record
    assert "ralph --check-config" in record


# =========================================================================
# 1. Pipeline Policy Fallback Tests
# =========================================================================


def test_pipeline_toml_syntax_error_fallback(tmp_path: Path) -> None:
    """Corrupt syntax in pipeline.toml falls back to built-in default."""
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
    """Invalid schema in pipeline.toml falls back to built-in default."""
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
    """strict_optional_files=True re-raises PolicyValidationError."""
    pipeline_file = tmp_path / "pipeline.toml"
    pipeline_file.write_text("broken = [syntax", encoding="utf-8")

    with pytest.raises(PolicyValidationError):
        load_policy(tmp_path, strict_optional_files=True)


# =========================================================================
# 2. Agents Policy Fallback Tests
# =========================================================================


def test_agents_toml_syntax_error_fallback(tmp_path: Path) -> None:
    """Corrupt syntax in agents.toml falls back to built-in default."""
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
    """Invalid schema in agents.toml falls back to built-in default."""
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
    """strict_optional_files=True re-raises PolicyValidationError on invalid schema."""
    agents_file = tmp_path / "agents.toml"
    agents_file.write_text("[agent_chains.planning]\nagents = []\n", encoding="utf-8")

    with pytest.raises(PolicyValidationError):
        load_agents_policy(tmp_path, strict_optional_files=True)


# =========================================================================
# 3. Artifacts Policy Fallback Tests
# =========================================================================


def test_artifacts_toml_syntax_error_fallback(tmp_path: Path) -> None:
    """Corrupt syntax in artifacts.toml falls back to built-in default."""
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
    """Invalid schema in artifacts.toml falls back to built-in default."""
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
    """strict_optional_files=True re-raises PolicyValidationError."""
    artifacts_file = tmp_path / "artifacts.toml"
    artifacts_file.write_text("invalid = {", encoding="utf-8")

    with pytest.raises(PolicyValidationError):
        load_policy(tmp_path, strict_optional_files=True)


# =========================================================================
# 4. Other User Policy TOMLs Fallback Tests
# =========================================================================


def test_other_user_policy_toml_fallback(tmp_path: Path) -> None:
    """Arbitrary user policy TOML falls back to empty dict on corruption."""
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
    """Permission-denied user policy TOML falls back to default with warning."""
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
    """Non-UTF-8 user policy TOML falls back to default with warning."""
    bad_encoding_file = tmp_path / "bad-encoding.toml"
    bad_bytes = b"\xff\xfe\x00\x01\x02"
    bad_encoding_file.write_bytes(bad_bytes)

    with _capture_warnings() as warnings:
        data = load_toml(bad_encoding_file, kind="policy", strict=False)

    assert data == {}
    assert bad_encoding_file.read_bytes() == bad_bytes
    assert len(warnings) == 1
    _assert_fallback_warning(warnings[0], bad_encoding_file, expected_kind="policy")


# =========================================================================
# 5. User Portfolio TOML Fallback Tests
# =========================================================================


def test_user_portfolio_toml_syntax_error_fallback(tmp_path: Path) -> None:
    """Corrupt syntax in user portfolio TOML falls back to built-in default."""
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
    """Invalid schema in user portfolio TOML falls back to built-in default."""
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
    """Missing user portfolio TOML silently returns built-in default without warnings."""
    missing_file = tmp_path / "does_not_exist.toml"

    with _capture_warnings() as warnings:
        portfolio = load_portfolio_toml(missing_file)

    expected_default = default_portfolio()
    assert portfolio.schema_version == expected_default.schema_version
    assert warnings == []


def test_user_portfolio_toml_strict_mode_raises(tmp_path: Path) -> None:
    """strict_optional_files=True raises on invalid portfolio TOML."""
    portfolio_file = tmp_path / "policy-portfolio.toml"
    portfolio_file.write_text("schema_version = 'invalid'\n", encoding="utf-8")

    with pytest.raises(PortfolioError):
        load_portfolio_toml(portfolio_file, strict_optional_files=True)


def test_user_portfolio_toml_second_read_error_warns_and_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the second read raises OSError, load_portfolio_toml emits a warning and returns default."""
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
    """parse_portfolio_toml with strict_optional_files=False falls back and warns."""
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


# =========================================================================
# 6. Deduplication and Re-warning Tests
# =========================================================================


def test_fallback_warning_deduplication(tmp_path: Path) -> None:
    """Repeated loads of identical corrupt file emit only one warning."""
    agents_file = tmp_path / "agents.toml"
    agents_file.write_text("broken = <<<", encoding="utf-8")

    with _capture_warnings() as warnings:
        load_policy(tmp_path)
        load_policy(tmp_path)
        load_agents_policy(tmp_path)

    assert len(warnings) == 1


def test_fallback_rewarns_when_content_changes(tmp_path: Path) -> None:
    """Changing content of corrupt file triggers a new warning."""
    pipeline_file = tmp_path / "pipeline.toml"
    pipeline_file.write_text("broken_1 = <<<", encoding="utf-8")

    with _capture_warnings() as warnings_1:
        load_policy(tmp_path)
    assert len(warnings_1) == 1

    pipeline_file.write_text("broken_2 = >>>", encoding="utf-8")
    with _capture_warnings() as warnings_2:
        load_policy(tmp_path)
    assert len(warnings_2) == 1
