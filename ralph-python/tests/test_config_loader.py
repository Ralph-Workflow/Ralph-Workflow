"""Unit tests for configuration loading and merging."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import JsonParserType, ReviewDepth, Verbosity
from ralph.config.loader import (
    GLOBAL_CONFIG_PATH,
    LOCAL_CONFIG_PATH,
    _deep_merge,
    load_config,
)
from ralph.config.models import AgentConfig, GeneralConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

DEFAULT_DEVELOPER_ITERS = 5
DEFAULT_REVIEWER_REVIEWS = 2
DEFAULT_VERBOSITY = 2


def _assert_validation_error(action: Callable[[], object]) -> None:
    with pytest.raises(Exception) as exc_info:
        action()

    assert exc_info.type.__name__ == "ValidationError"


def _assert_validation_error_or_system_exit(action: Callable[[], object]) -> None:
    try:
        action()
    except SystemExit:
        return
    except Exception as exc:
        assert exc.__class__.__name__ == "ValidationError"
        return

    pytest.fail("Expected ValidationError or SystemExit")


def test_deep_merge_simple() -> None:
    """Test basic dictionary merge."""
    base: dict[str, object] = {"a": 1, "b": 2}
    override: dict[str, object] = {"b": 3, "c": 4}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": 3, "c": 4}


def test_deep_merge_nested() -> None:
    """Test nested dictionary merge."""
    base: dict[str, object] = {"general": {"a": 1, "b": 2}}
    override: dict[str, object] = {"general": {"b": 3, "c": 4}}
    result = _deep_merge(base, override)
    assert result == {"general": {"a": 1, "b": 3, "c": 4}}


def test_deep_merge_override_wins() -> None:
    """Test that override values take precedence."""
    base: dict[str, object] = {"a": 1, "b": {"x": 1, "y": 2}}
    override: dict[str, object] = {"b": {"y": 3, "z": 4}}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": {"x": 1, "y": 3, "z": 4}}


def test_load_config_with_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test loading config with default values."""
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", tmp_path / LOCAL_CONFIG_PATH.name)

    config = load_config()
    assert config.general.developer_iters == DEFAULT_DEVELOPER_ITERS
    assert config.general.reviewer_reviews == DEFAULT_REVIEWER_REVIEWS
    assert config.general.workflow.checkpoint_enabled is True


def test_load_config_validation_error() -> None:
    """Test that invalid config raises ValidationError."""
    _assert_validation_error_or_system_exit(
        lambda: load_config(cli_overrides={"general": {"developer_iters": -1}})
    )


def test_unified_config_frozen() -> None:
    """Test that UnifiedConfig is immutable (frozen)."""
    config = load_config()
    _assert_validation_error(lambda: setattr(config.general, "developer_iters", 10))


def test_agent_config_frozen() -> None:
    """Test that AgentConfig is immutable (frozen)."""
    agent = AgentConfig(cmd="test")
    _assert_validation_error(lambda: setattr(agent, "cmd", "changed"))


def test_general_config_defaults() -> None:
    """Test GeneralConfig default values."""
    config = GeneralConfig()
    assert config.developer_iters == DEFAULT_DEVELOPER_ITERS
    assert config.reviewer_reviews == DEFAULT_REVIEWER_REVIEWS
    assert config.verbosity == DEFAULT_VERBOSITY
    assert config.workflow.checkpoint_enabled is True
    assert config.execution.isolation_mode is True


def test_review_depth_enum() -> None:
    """Test ReviewDepth enum values."""
    assert str(ReviewDepth.STANDARD) == "standard"
    assert str(ReviewDepth.COMPREHENSIVE) == "comprehensive"
    assert str(ReviewDepth.SECURITY) == "security"
    assert str(ReviewDepth.INCREMENTAL) == "incremental"


def test_verbosity_enum() -> None:
    """Test Verbosity enum values."""
    assert str(Verbosity.QUIET) == "quiet"
    assert str(Verbosity.NORMAL) == "normal"
    assert str(Verbosity.VERBOSE) == "verbose"
    assert str(Verbosity.FULL) == "full"
    assert str(Verbosity.DEBUG) == "debug"


def test_json_parser_type_enum() -> None:
    """Test JsonParserType enum values."""
    assert str(JsonParserType.CLAUDE) == "claude"
    assert str(JsonParserType.CODEX) == "codex"
    assert str(JsonParserType.GEMINI) == "gemini"
    assert str(JsonParserType.OPENCODE) == "opencode"
    assert str(JsonParserType.GENERIC) == "generic"
