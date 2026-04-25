"""Unit tests for configuration loading and merging."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport, JsonParserType, ReviewDepth, Verbosity
from ralph.config.loader import (
    GLOBAL_CONFIG_PATH,
    LOCAL_CONFIG_PATH,
    _deep_merge,
    load_config,
)
from ralph.config.models import AgentConfig, GeneralConfig
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_DEVELOPER_ITERS = 5
DEFAULT_REVIEWER_REVIEWS = 2
DEFAULT_VERBOSITY = 2
XDG_DEVELOPER_ITERS = 8
LOCAL_DEVELOPER_ITERS = 3


def _scope_for(path: Path) -> WorkspaceScope:
    return WorkspaceScope(path)


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

    config = load_config(workspace_scope=_scope_for(tmp_path))
    assert config.general.developer_iters == DEFAULT_DEVELOPER_ITERS
    assert config.general.reviewer_reviews == DEFAULT_REVIEWER_REVIEWS
    assert config.general.workflow.checkpoint_enabled is True


def test_load_config_validation_error() -> None:
    """Test that invalid config raises ValidationError."""
    _assert_validation_error_or_system_exit(
        lambda: load_config(
            cli_overrides={"general": {"developer_iters": -1}},
            workspace_scope=_scope_for(Path.cwd()),
        )
    )


def test_load_config_supports_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_home = tmp_path / "xdg-config"
    config_home.mkdir()
    (config_home / "ralph-workflow.toml").write_text(
        "[general]\ndeveloper_iters = 8\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setattr(
        "ralph.config.loader.LOCAL_CONFIG_PATH", tmp_path / ".agent" / "ralph-workflow.toml"
    )

    config = load_config(workspace_scope=_scope_for(tmp_path))

    assert config.general.developer_iters == XDG_DEVELOPER_ITERS


def test_load_config_converts_nested_chain_and_drain_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )
    local_path = tmp_path / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text(
        "\n".join(
            [
                "[agent_chains.commit_chain]",
                'agents = ["claude"]',
                "[agent_drains.commit]",
                'chain = "commit_chain"',
                "[agent_drains.review]",
                'chain = "commit_chain"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", local_path)

    config = load_config(workspace_scope=_scope_for(tmp_path))

    assert config.agent_chains == {"commit_chain": ["claude"]}
    assert config.agent_drains == {"commit": "commit_chain", "review": "commit_chain"}


def test_load_config_local_normalized_tables_override_xdg_global(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_home = tmp_path / "xdg-config"
    config_home.mkdir()
    (config_home / "ralph-workflow.toml").write_text(
        (
            "[agent_chains.commit_chain]\n"
            'agents = ["claude"]\n'
            "[agent_drains.commit]\n"
            'chain = "commit_chain"\n'
            "[general]\n"
            "developer_iters = 8\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    local_path = tmp_path / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text(
        (
            "[agent_chains.commit_chain]\n"
            'agents = ["codex"]\n'
            "[agent_drains.commit]\n"
            'chain = "commit_chain"\n'
            "[general]\n"
            "developer_iters = 3\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", local_path)

    config = load_config(workspace_scope=_scope_for(tmp_path))

    assert config.general.developer_iters == LOCAL_DEVELOPER_ITERS
    assert config.agent_chains["commit_chain"] == ["codex"]
    assert config.agent_drains["commit"] == "commit_chain"


def test_unified_config_frozen() -> None:
    """Test that UnifiedConfig is immutable (frozen)."""
    config = load_config(workspace_scope=_scope_for(Path.cwd()))
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
    assert config.execution.force_universal_prompt is False


def test_general_config_does_not_expose_removed_field() -> None:
    """Test that the dead field is removed."""
    field_name = "max_dev" + "_continuations"
    assert field_name not in GeneralConfig.model_fields


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


def test_agent_transport_enum() -> None:
    assert str(AgentTransport.CLAUDE) == "claude"
    assert str(AgentTransport.CODEX) == "codex"
    assert str(AgentTransport.OPENCODE) == "opencode"
    assert str(AgentTransport.GENERIC) == "generic"
