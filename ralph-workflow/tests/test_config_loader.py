"""Unit tests for configuration loading and merging."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.config.enums import AgentTransport, JsonParserType, Verbosity
from ralph.config.loader import (
    GLOBAL_CONFIG_PATH,
    LOCAL_CONFIG_PATH,
    deep_merge,
    load_config,
)
from ralph.config.models import AgentConfig, GeneralConfig
from ralph.timeout_defaults import (
    CHILD_EXIT_RECONCILE_SECONDS,
    CHILD_HEARTBEAT_TTL_SECONDS,
    CHILD_PROGRESS_TTL_SECONDS,
    CHILD_STALE_LABEL_TTL_SECONDS,
    CPU_IDLE_SECONDS,
    DESCENDANT_WAIT_POLL_SECONDS,
    DESCENDANT_WAIT_TIMEOUT_SECONDS,
    DRAIN_WINDOW_SECONDS,
    IDLE_POLL_INTERVAL_SECONDS,
    IDLE_TIMEOUT_SECONDS,
    LOG_GROWTH_SECONDS,
    MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS,
    MAX_WAITING_ON_CHILD_SECONDS,
    OS_DESCENDANT_ONLY_CEILING_SECONDS,
    OS_DESCENDANT_ONLY_SUSPECT_SECONDS,
    PARENT_EXIT_GRACE_SECONDS,
    PROCESS_EXIT_WAIT_SECONDS,
    SUSPECT_WAITING_ON_CHILD_SECONDS,
    WAITING_STATUS_INTERVAL_SECONDS,
)
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
    result = deep_merge(base, override)
    assert result == {"a": 1, "b": 3, "c": 4}


def test_deep_merge_nested() -> None:
    """Test nested dictionary merge."""
    base: dict[str, object] = {"general": {"a": 1, "b": 2}}
    override: dict[str, object] = {"general": {"b": 3, "c": 4}}
    result = deep_merge(base, override)
    assert result == {"general": {"a": 1, "b": 3, "c": 4}}


def test_deep_merge_override_wins() -> None:
    """Test that override values take precedence."""
    base: dict[str, object] = {"a": 1, "b": {"x": 1, "y": 2}}
    override: dict[str, object] = {"b": {"y": 3, "z": 4}}
    result = deep_merge(base, override)
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

    assert config.agent_chains["commit_chain"].agents == ["claude"]
    assert config.agent_drains["commit"].chain == "commit_chain"
    assert config.agent_drains["review"].chain == "commit_chain"


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

    assert config.agent_chains["commit_chain"].agents == ["codex"]
    assert config.agent_drains["commit"].chain == "commit_chain"


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


def test_general_config_does_not_expose_removed_field() -> None:
    """Test that the dead field is removed."""
    field_name = "max_dev" + "_continuations"
    assert field_name not in GeneralConfig.model_fields


def test_general_config_does_not_expose_removed_execution_flags() -> None:
    """Removed review-era execution flags must not remain in GeneralConfig."""
    assert "execution" not in GeneralConfig.model_fields
    assert "behavior" not in GeneralConfig.model_fields


def test_general_config_does_not_expose_removed_force_universal_prompt() -> None:
    """force_universal_prompt and related review-era fields were removed as dead code."""
    for field_name in (
        "force_universal_prompt",
        "auto_detect_stack",
        "interactive",
        "strict_validation",
    ):
        assert field_name not in GeneralConfig.model_fields


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
    assert cfg.agent_child_progress_ttl_seconds == 45.0


def test_general_config_child_heartbeat_ttl_default() -> None:
    cfg = GeneralConfig()
    assert cfg.agent_child_heartbeat_ttl_seconds == 15.0


def test_general_config_child_stale_label_ttl_default() -> None:
    cfg = GeneralConfig()
    assert cfg.agent_child_stale_label_ttl_seconds == 10.0


def test_general_config_child_exit_reconcile_default() -> None:
    cfg = GeneralConfig()
    assert cfg.agent_child_exit_reconcile_seconds == 5.0


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

    assert config.general.agent_child_progress_ttl_seconds == 90.0


# ---------------------------------------------------------------------------
# OS-descendant-only and probe config knobs
# ---------------------------------------------------------------------------

_OS_DESCENDANT_ONLY_CEILING = 300.0
_OS_DESCENDANT_ONLY_SUSPECT = 60.0
_CPU_IDLE = 60.0
_LOG_GROWTH = 30.0


def test_general_config_os_descendant_only_ceiling_default() -> None:
    cfg = GeneralConfig()
    assert cfg.agent_os_descendant_only_ceiling_seconds == _OS_DESCENDANT_ONLY_CEILING


def test_general_config_os_descendant_only_suspect_default() -> None:
    cfg = GeneralConfig()
    assert cfg.agent_os_descendant_only_suspect_seconds == _OS_DESCENDANT_ONLY_SUSPECT


def test_general_config_cpu_idle_default() -> None:
    cfg = GeneralConfig()
    assert cfg.agent_cpu_idle_seconds == _CPU_IDLE


def test_general_config_log_growth_default() -> None:
    cfg = GeneralConfig()
    assert cfg.agent_log_growth_seconds == _LOG_GROWTH


def test_general_config_os_descendant_only_ceiling_can_be_none() -> None:
    """os_descendant_only_ceiling may be explicitly disabled by setting to null."""
    cfg = GeneralConfig(agent_os_descendant_only_ceiling_seconds=None)
    assert cfg.agent_os_descendant_only_ceiling_seconds is None


def test_general_config_cpu_idle_can_be_none() -> None:
    """cpu_idle may be explicitly disabled by setting to null."""
    cfg = GeneralConfig(agent_cpu_idle_seconds=None)
    assert cfg.agent_cpu_idle_seconds is None


def test_general_config_log_growth_can_be_none() -> None:
    """log_growth may be explicitly disabled by setting to null."""
    cfg = GeneralConfig(agent_log_growth_seconds=None)
    assert cfg.agent_log_growth_seconds is None


def test_load_config_os_descendant_only_ceiling_roundtrips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )
    local_path = tmp_path / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text(
        "[general]\nagent_os_descendant_only_ceiling_seconds = 90.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", local_path)

    config = load_config(workspace_scope=_scope_for(tmp_path))
    assert config.general.agent_os_descendant_only_ceiling_seconds == 90.0


def test_load_config_cpu_idle_roundtrips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )
    local_path = tmp_path / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text(
        "[general]\nagent_cpu_idle_seconds = 45.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", local_path)

    config = load_config(workspace_scope=_scope_for(tmp_path))
    assert config.general.agent_cpu_idle_seconds == 45.0


def test_load_config_log_growth_roundtrips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )
    local_path = tmp_path / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text(
        "[general]\nagent_log_growth_seconds = 15.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", local_path)

    config = load_config(workspace_scope=_scope_for(tmp_path))
    assert config.general.agent_log_growth_seconds == 15.0


# ---------------------------------------------------------------------------
# Shared-constant round-trip assertions (timeout_defaults.py source of truth)
# ---------------------------------------------------------------------------


def test_config_defaults_match_timeout_defaults_constants() -> None:
    """GeneralConfig defaults match the shared constants in ralph.timeout_defaults.

    This test is the sentinel that prevents the three timeout-default layers
    (idle_watchdog.TimeoutPolicy, invoke._CHILD_* constants, and config field
    defaults) from drifting away from each other independently.
    """
    cfg = GeneralConfig()

    assert cfg.agent_idle_timeout_seconds == IDLE_TIMEOUT_SECONDS
    assert cfg.agent_idle_drain_window_seconds == DRAIN_WINDOW_SECONDS
    assert cfg.agent_idle_max_waiting_on_child_seconds == MAX_WAITING_ON_CHILD_SECONDS
    assert cfg.agent_idle_poll_interval_seconds == IDLE_POLL_INTERVAL_SECONDS
    assert cfg.agent_parent_exit_grace_seconds == PARENT_EXIT_GRACE_SECONDS
    assert cfg.agent_descendant_wait_timeout_seconds == DESCENDANT_WAIT_TIMEOUT_SECONDS
    assert cfg.agent_descendant_wait_poll_seconds == DESCENDANT_WAIT_POLL_SECONDS
    assert cfg.agent_process_exit_wait_seconds == PROCESS_EXIT_WAIT_SECONDS
    assert cfg.agent_waiting_status_interval_seconds == WAITING_STATUS_INTERVAL_SECONDS
    assert cfg.agent_suspect_waiting_on_child_seconds == SUSPECT_WAITING_ON_CHILD_SECONDS
    assert (
        cfg.agent_idle_no_progress_waiting_on_child_seconds
        == MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS
    )
    assert cfg.agent_child_progress_ttl_seconds == CHILD_PROGRESS_TTL_SECONDS
    assert cfg.agent_child_heartbeat_ttl_seconds == CHILD_HEARTBEAT_TTL_SECONDS
    assert cfg.agent_child_stale_label_ttl_seconds == CHILD_STALE_LABEL_TTL_SECONDS
    assert cfg.agent_child_exit_reconcile_seconds == CHILD_EXIT_RECONCILE_SECONDS
    assert cfg.agent_os_descendant_only_ceiling_seconds == OS_DESCENDANT_ONLY_CEILING_SECONDS
    assert cfg.agent_os_descendant_only_suspect_seconds == OS_DESCENDANT_ONLY_SUSPECT_SECONDS
    assert cfg.agent_cpu_idle_seconds == CPU_IDLE_SECONDS
    assert cfg.agent_log_growth_seconds == LOG_GROWTH_SECONDS


def test_timeout_policy_defaults_match_timeout_defaults_constants() -> None:
    """TimeoutPolicy field defaults match the shared constants in ralph.timeout_defaults.

    Ensures idle_watchdog.TimeoutPolicy cannot drift from config defaults.
    """
    policy = TimeoutPolicy(idle_timeout_seconds=None)

    assert policy.drain_window_seconds == DRAIN_WINDOW_SECONDS
    assert policy.max_waiting_on_child_seconds == MAX_WAITING_ON_CHILD_SECONDS
    assert policy.idle_poll_interval_seconds == IDLE_POLL_INTERVAL_SECONDS
    assert policy.parent_exit_grace_seconds == PARENT_EXIT_GRACE_SECONDS
    assert policy.descendant_wait_timeout_seconds == DESCENDANT_WAIT_TIMEOUT_SECONDS
    assert policy.descendant_wait_poll_seconds == DESCENDANT_WAIT_POLL_SECONDS
    assert policy.process_exit_wait_seconds == PROCESS_EXIT_WAIT_SECONDS
    assert policy.waiting_status_interval_seconds == WAITING_STATUS_INTERVAL_SECONDS
    assert policy.suspect_waiting_on_child_seconds == SUSPECT_WAITING_ON_CHILD_SECONDS
    assert (
        policy.max_waiting_on_child_no_progress_seconds == MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS
    )
    assert policy.os_descendant_only_ceiling_seconds == OS_DESCENDANT_ONLY_CEILING_SECONDS
    assert policy.os_descendant_only_suspect_seconds == OS_DESCENDANT_ONLY_SUSPECT_SECONDS
    assert policy.cpu_idle_seconds == CPU_IDLE_SECONDS
    assert policy.log_growth_seconds == LOG_GROWTH_SECONDS
