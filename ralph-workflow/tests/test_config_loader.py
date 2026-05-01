"""Unit tests for configuration loading and merging."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from ralph.config.enums import AgentTransport, JsonParserType, ReviewDepth, Verbosity
from ralph.config.loader import (
    GLOBAL_CONFIG_PATH,
    LOCAL_CONFIG_PATH,
    _deep_merge,
    load_config,
)
from ralph.config.models import AgentConfig, GeneralConfig
from ralph.workspace.scope import WorkspaceScope

DEFAULT_VERBOSITY = 2

ACTIVE_AGENT_POLICY = (
    "[agent_chains]\n"
    'planning = ["claude"]\n'
    'development = ["claude", "opencode"]\n'
    'analysis = ["claude"]\n'
    'review = ["claude"]\n'
    'fix = ["claude"]\n'
    'commit = ["claude"]\n'
    "\n"
    "[agent_drains]\n"
    'planning = "planning"\n'
    'development = "development"\n'
    'development_analysis = "analysis"\n'
    'development_commit = "commit"\n'
    'review = "review"\n'
    'review_analysis = "analysis"\n'
    'review_commit = "commit"\n'
    'fix = "fix"\n'
)


def _scope_for(path: Path) -> WorkspaceScope:
    return WorkspaceScope(path)


def _assert_validation_error(action: Callable[[], object]) -> None:
    with pytest.raises(Exception) as exc_info:
        action()

    assert exc_info.type.__name__ == "ValidationError"



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


def test_load_config_without_agent_policy_tables_leaves_agent_policy_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing agent policy config must not be silently filled by Python defaults."""
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", tmp_path / LOCAL_CONFIG_PATH.name)

    config = load_config(workspace_scope=_scope_for(tmp_path))
    assert config.agent_chains == {}
    assert config.agent_drains == {}
    assert config.general.workflow.checkpoint_enabled is True



def test_load_config_supports_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_home = tmp_path / "xdg-config"
    config_home.mkdir()
    (config_home / "ralph-workflow.toml").write_text(
        ACTIVE_AGENT_POLICY,
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setattr(
        "ralph.config.loader.LOCAL_CONFIG_PATH", tmp_path / ".agent" / "ralph-workflow.toml"
    )

    config = load_config(workspace_scope=_scope_for(tmp_path))

    assert config.agent_chains != {}


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
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", local_path)

    config = load_config(workspace_scope=_scope_for(tmp_path))

    assert config.agent_chains["commit_chain"] == ["codex"]
    assert config.agent_drains["commit"] == "commit_chain"


def test_unified_config_frozen() -> None:
    """Test that UnifiedConfig is immutable (frozen)."""
    config = load_config(workspace_scope=_scope_for(Path.cwd()))
    _assert_validation_error(lambda: setattr(config.general, "verbosity", 99))


def test_agent_config_frozen() -> None:
    """Test that AgentConfig is immutable (frozen)."""
    agent = AgentConfig(cmd="test")
    _assert_validation_error(lambda: setattr(agent, "cmd", "changed"))


def test_general_config_defaults() -> None:
    """Test GeneralConfig default values."""
    config = GeneralConfig()
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


_DEFAULT_WAITING_STATUS_INTERVAL = 30.0
_DEFAULT_SUSPECT_THRESHOLD = 600.0
_CUSTOM_WAITING_INTERVAL = 60.0
_CUSTOM_SUSPECT_THRESHOLD = 120.0
_SMALL_MAX_WAITING = 100.0
_LARGE_SUSPECT = 200.0
_VALID_SUSPECT = 300.0


def test_general_config_waiting_status_interval_defaults() -> None:
    """New waiting-status interval field has correct default."""
    cfg = GeneralConfig()
    assert cfg.agent_waiting_status_interval_seconds == _DEFAULT_WAITING_STATUS_INTERVAL


def test_general_config_suspect_waiting_on_child_defaults() -> None:
    """New suspicion threshold field has correct default."""
    cfg = GeneralConfig()
    assert cfg.agent_suspect_waiting_on_child_seconds == _DEFAULT_SUSPECT_THRESHOLD


def test_general_config_suspect_waiting_on_child_can_be_none() -> None:
    """Suspicion threshold may be explicitly disabled."""
    cfg = GeneralConfig(agent_suspect_waiting_on_child_seconds=None)
    assert cfg.agent_suspect_waiting_on_child_seconds is None


def test_general_config_suspect_above_max_raises() -> None:
    """suspect_waiting_on_child >= idle_max_waiting_on_child is invalid."""
    _assert_validation_error(
        lambda: GeneralConfig(
            agent_idle_max_waiting_on_child_seconds=_SMALL_MAX_WAITING,
            agent_suspect_waiting_on_child_seconds=_LARGE_SUSPECT,
        )
    )


def test_general_config_suspect_equal_to_max_raises() -> None:
    """suspect_waiting_on_child == idle_max_waiting_on_child is invalid."""
    _assert_validation_error(
        lambda: GeneralConfig(
            agent_idle_max_waiting_on_child_seconds=_SMALL_MAX_WAITING,
            agent_suspect_waiting_on_child_seconds=_SMALL_MAX_WAITING,
        )
    )


def test_general_config_suspect_below_max_valid() -> None:
    """suspect_waiting_on_child < idle_max_waiting_on_child is valid."""
    cfg = GeneralConfig(
        agent_idle_max_waiting_on_child_seconds=1800.0,
        agent_suspect_waiting_on_child_seconds=_VALID_SUSPECT,
    )
    assert cfg.agent_suspect_waiting_on_child_seconds == _VALID_SUSPECT


def test_load_config_waiting_status_interval_roundtrips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Operator-set waiting_status_interval_seconds survives config load."""
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )
    local_path = tmp_path / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text(
        f"[general]\nagent_waiting_status_interval_seconds = {_CUSTOM_WAITING_INTERVAL}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", local_path)

    config = load_config(workspace_scope=_scope_for(tmp_path))

    assert config.general.agent_waiting_status_interval_seconds == _CUSTOM_WAITING_INTERVAL


def test_load_config_suspect_threshold_roundtrips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Operator-set agent_suspect_waiting_on_child_seconds survives config load."""
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )
    local_path = tmp_path / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text(
        f"[general]\nagent_suspect_waiting_on_child_seconds = {_CUSTOM_SUSPECT_THRESHOLD}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", local_path)

    config = load_config(workspace_scope=_scope_for(tmp_path))

    assert config.general.agent_suspect_waiting_on_child_seconds == _CUSTOM_SUSPECT_THRESHOLD


# ---------------------------------------------------------------------------
# Child-liveness TTL config knobs
# ---------------------------------------------------------------------------


def test_general_config_child_progress_ttl_default() -> None:
    cfg = GeneralConfig()
    assert cfg.agent_child_progress_ttl_seconds == 45.0  # noqa: PLR2004


def test_general_config_child_heartbeat_ttl_default() -> None:
    cfg = GeneralConfig()
    assert cfg.agent_child_heartbeat_ttl_seconds == 15.0  # noqa: PLR2004


def test_general_config_child_stale_label_ttl_default() -> None:
    cfg = GeneralConfig()
    assert cfg.agent_child_stale_label_ttl_seconds == 10.0  # noqa: PLR2004


def test_general_config_child_exit_reconcile_default() -> None:
    cfg = GeneralConfig()
    assert cfg.agent_child_exit_reconcile_seconds == 5.0  # noqa: PLR2004


def test_load_config_child_progress_ttl_roundtrips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )
    local_path = tmp_path / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text(
        "[general]\nagent_child_progress_ttl_seconds = 90.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", local_path)

    config = load_config(workspace_scope=_scope_for(tmp_path))

    assert config.general.agent_child_progress_ttl_seconds == 90.0  # noqa: PLR2004
