"""Unit tests for configuration loading and merging."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.config.loader import (
    GLOBAL_CONFIG_PATH,
    LOCAL_CONFIG_PATH,
    _deep_merge,
    load_config,
)


def test_deep_merge_simple() -> None:
    """Test basic dictionary merge."""
    base = {"a": 1, "b": 2}
    override = {"b": 3, "c": 4}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": 3, "c": 4}


def test_deep_merge_nested() -> None:
    """Test nested dictionary merge."""
    base = {"general": {"a": 1, "b": 2}}
    override = {"general": {"b": 3, "c": 4}}
    result = _deep_merge(base, override)
    assert result == {"general": {"a": 1, "b": 3, "c": 4}}


def test_deep_merge_override_wins() -> None:
    """Test that override values take precedence."""
    base = {"a": 1, "b": {"x": 1, "y": 2}}
    override = {"b": {"y": 3, "z": 4}}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": {"x": 1, "y": 3, "z": 4}}


def test_load_config_with_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test loading config with default values."""
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )
    monkeypatch.setattr(
        "ralph.config.loader.LOCAL_CONFIG_PATH", tmp_path / LOCAL_CONFIG_PATH.name
    )

    config = load_config()
    assert config.general.developer_iters == 5
    assert config.general.reviewer_reviews == 2
    assert config.general.workflow.checkpoint_enabled is True


def test_load_config_validation_error() -> None:
    """Test that invalid config raises ValidationError."""
    with pytest.raises((ValidationError, SystemExit)):
        load_config(cli_overrides={"general": {"developer_iters": -1}})


def test_unified_config_frozen() -> None:
    """Test that UnifiedConfig is immutable (frozen)."""
    config = load_config()
    with pytest.raises(ValidationError):
        config.general.developer_iters = 10


def test_agent_config_frozen() -> None:
    """Test that AgentConfig is immutable (frozen)."""
    from ralph.config.models import AgentConfig

    agent = AgentConfig(cmd="test")
    with pytest.raises(ValidationError):
        agent.cmd = "changed"


def test_general_config_defaults() -> None:
    """Test GeneralConfig default values."""
    from ralph.config.models import GeneralConfig

    config = GeneralConfig()
    assert config.developer_iters == 5
    assert config.reviewer_reviews == 2
    assert config.verbosity == 2
    assert config.workflow.checkpoint_enabled is True
    assert config.execution.isolation_mode is True


def test_review_depth_enum() -> None:
    """Test ReviewDepth enum values."""
    from ralph.config.enums import ReviewDepth

    assert ReviewDepth.STANDARD.value == "standard"
    assert ReviewDepth.COMPREHENSIVE.value == "comprehensive"
    assert ReviewDepth.SECURITY.value == "security"
    assert ReviewDepth.INCREMENTAL.value == "incremental"


def test_verbosity_enum() -> None:
    """Test Verbosity enum values."""
    from ralph.config.enums import Verbosity

    assert Verbosity.QUIET.value == "quiet"
    assert Verbosity.NORMAL.value == "normal"
    assert Verbosity.VERBOSE.value == "verbose"
    assert Verbosity.FULL.value == "full"
    assert Verbosity.DEBUG.value == "debug"


def test_json_parser_type_enum() -> None:
    """Test JsonParserType enum values."""
    from ralph.config.enums import JsonParserType

    assert JsonParserType.CLAUDE.value == "claude"
    assert JsonParserType.CODEX.value == "codex"
    assert JsonParserType.GEMINI.value == "gemini"
    assert JsonParserType.OPENCODE.value == "opencode"
    assert JsonParserType.GENERIC.value == "generic"
