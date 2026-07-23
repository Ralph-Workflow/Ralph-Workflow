"""Unit tests for configuration loading and merging."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from loguru import logger

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.config.config_error_messages import format_config_validation_error
from ralph.config.enums import AgentTransport, JsonParserType, Verbosity
from ralph.config.loader import (
    GLOBAL_CONFIG_PATH,
    LOCAL_CONFIG_PATH,
    ConfigTomlError,
    deep_merge,
    load_config,
    load_toml,
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


def test_load_toml_malformed_config_names_file_and_fix(tmp_path: Path) -> None:
    """A malformed user config must not silently fall back to defaults."""
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text("[general\nverbosity = 2\n", encoding="utf-8")

    with pytest.raises(ConfigTomlError) as exc_info:
        load_toml(config_path)

    message = str(exc_info.value)
    assert config_path.name in message
    assert "What failed:" in message
    assert "Why it matters:" in message
    assert "Fix:" in message


def test_load_config_unknown_field_warns_with_field_and_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A misspelled config field must be visible instead of being silently ignored."""
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text("[general]\nverbosuty = 1\n", encoding="utf-8")
    monkeypatch.setattr("ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / "missing-global.toml")
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        config = load_config(config_path=config_path)
    finally:
        logger.remove(sink_id)

    assert config.general.verbosity == DEFAULT_VERBOSITY
    warning = "\n".join(records)
    assert "verbosuty" in warning
    assert str(config_path) in warning


def test_collect_unknown_config_fields_names_nested_typo_with_suggestion() -> None:
    """``collect_unknown_config_fields`` must name nested-subtable typos AND suggest the canonical name.

    AC-01: typo'd keys in nested subtables like ``general.wrokflow`` or
    ``general.workflow.checkpont_enabled`` must be surfaced with a
    ``did you mean`` suggestion. Agent chain names like ``[agent_chains.planning]``
    are USER-DEFINED and must NOT be warned about.
    """
    from pathlib import Path

    from ralph.config.loader import collect_unknown_config_fields

    data = {
        "general": {
            "workflow": {"checkpont_enabled": True},  # typo of checkpoint_enabled
            "wrokflow": {"checkpoint_enabled": True},  # typo of workflow
        },
        "agent_chains": {
            "planning": ["claude"],  # user-defined name; must NOT be warned
            "custom_chain_name": ["claude"],  # also user-defined; must NOT be warned
        },
    }
    lines = collect_unknown_config_fields(data, Path("ralph-workflow.toml"))

    # Nested typos must be surfaced with the dotted path.
    assert any("general.workflow.checkpont_enabled" in line for line in lines), (
        f"Expected nested typo 'general.workflow.checkpont_enabled' in "
        f"unknown-field findings, got: {lines}"
    )
    assert any("general.wrokflow" in line for line in lines), (
        f"Expected nested typo 'general.wrokflow' in unknown-field "
        f"findings, got: {lines}"
    )
    # Each finding must include a 'did you mean' suggestion pointing at
    # the canonical field name.
    for line in lines:
        if "general.wrokflow" in line:
            assert "workflow" in line, (
                f"Expected canonical suggestion 'workflow' in line "
                f"{line!r} (did-you-mean path)"
            )
    # User-defined chain names must NOT be warned about.
    assert not any("agent_chains.planning" in line for line in lines), (
        f"User-defined agent chain names must not be warned about; got: {lines}"
    )
    assert not any("custom_chain_name" in line for line in lines), (
        f"User-defined agent chain names must not be warned about; got: {lines}"
    )


def test_collect_unknown_config_fields_warns_on_unknown_agents_subkey() -> None:
    """A typo INSIDE an agent block must be detected.

    ``[agents.claude].yolo_flg`` is a leaf-typo (real field is ``yolo_flag``);
    a single-character typo must surface with a suggestion.
    """
    from pathlib import Path

    from ralph.config.loader import collect_unknown_config_fields

    data = {
        "agents": {
            "claude": {"yolo_flg": "--dangerously-skip-permissions"},
        },
    }
    lines = collect_unknown_config_fields(data, Path("ralph-workflow.toml"))
    assert any("agents.claude.yolo_flg" in line for line in lines), (
        f"Expected leaf typo 'agents.claude.yolo_flg' to surface, got: {lines}"
    )
    # And the suggestion should be the canonical field name.
    for line in lines:
        if "yolo_flg" in line:
            assert "yolo_flag" in line, (
                f"Expected canonical suggestion 'yolo_flag' in line "
                f"{line!r}, got: {line!r}"
            )


def test_collect_unknown_config_fields_clean_when_schema_matches() -> None:
    """A clean config yields zero unknown-field findings."""
    from pathlib import Path

    from ralph.config.loader import collect_unknown_config_fields

    lines = collect_unknown_config_fields({}, Path("ralph-workflow.toml"))
    assert lines == [], (
        f"Empty config should produce zero unknown-field findings, got: {lines}"
    )


def test_load_config_warns_on_unknown_field_in_propagated_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A typo in an inherited (propagated) ancestor config must surface too.

    AC-01 follow-up: ``load_config`` merges every
    ``workspace_scope.propagated_config_paths`` file into the effective
    configuration but must call ``warn_unknown_fields`` for each
    inherited source path, not only for global + local. An unknown key
    in an effective ancestor config must therefore be visible in the
    loader warning, not silently ignored.
    """
    from ralph.workspace.scope import WorkspaceScope

    propagated_path = tmp_path / "parent" / ".agent" / "ralph-workflow.toml"
    propagated_path.parent.mkdir(parents=True)
    propagated_path.write_text(
        "[general]\nwrokflow = { checkpoint_enabled = true }\n",
        encoding="utf-8",
    )
    local_path = tmp_path / "child" / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text("", encoding="utf-8")

    monkeypatch.setattr("ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / "missing-global.toml")
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        load_config(
            workspace_scope=WorkspaceScope(
                root=tmp_path / "child",
                local_config_path=local_path,
                propagated_config_paths=(propagated_path,),
            )
        )
    finally:
        logger.remove(sink_id)

    warning = "\n".join(records)
    assert "general.wrokflow" in warning, (
        f"Expected the propagated-ancestor typo 'general.wrokflow' in the "
        f"loader warning, got: {warning!r}"
    )
    assert str(propagated_path) in warning, (
        f"Expected the propagated-ancestor path to be named in the loader "
        f"warning, got: {warning!r}"
    )
    # The fix-it suggestion must include the canonical field name.
    assert "workflow" in warning, (
        f"Expected canonical 'workflow' suggestion in propagated-ancestor "
        f"warning, got: {warning!r}"
    )


def test_invalid_value_message_names_rejected_and_allowed() -> None:
    """A Pydantic ValidationError must surface the rejected value and allowed enum set.

    AC-02: a config validation error must name the rejected value (so the
    operator can paste it back from the message) AND the allowed values
    (so they do not need to consult the Pydantic docs).
    """
    from pydantic import ValidationError

    from ralph.config.models import UnifiedConfig

    # json_parser is a closed enum; pass an out-of-set value.
    try:
        UnifiedConfig.model_validate(
            {"agents": {"bad": {"cmd": "x", "json_parser": "NOTREAL"}}}
        )
    except ValidationError as exc:
        message = format_config_validation_error(exc, Path("ralph-workflow.toml"))
        # The message must name the rejected value.
        assert "NOTREAL" in message, (
            f"Expected the rejected value 'NOTREAL' to be named in the "
            f"config-error message, got: {message!r}"
        )
        # The message must list the allowed enum values.
        assert "'claude'" in message and "'codex'" in message, (
            f"Expected the allowed json_parser enum values to be listed in "
            f"the config-error message, got: {message!r}"
        )
        # The message must keep the what/why/fix envelope.
        for marker in ("What failed:", "Why it matters:", "Fix:"):
            assert marker in message, (
                f"Expected {marker!r} in the config-error envelope, "
                f"got: {message!r}"
            )


def test_load_config_missing_agent_command_logs_ralph_authored_remediation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing required agent fields must show a file, field, and concrete fix."""
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text("[agents.broken]\n", encoding="utf-8")
    monkeypatch.setattr("ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / "missing-global.toml")
    records: list[str] = []
    sink_id = logger.add(records.append, level="ERROR", format="{message}")
    try:
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=config_path)
    finally:
        logger.remove(sink_id)

    assert exc_info.value.code == 1
    message = "\n".join(records)
    assert "What failed:" in message
    assert "Why it matters:" in message
    assert "Fix:" in message
    assert str(config_path) in message
    assert "agents.broken.cmd" in message
    # The per-field line lists the rejected value and the docstring anchor
    # for what to set, so the operator gets both the why and a concrete fix.
    assert "Field required" in message
    assert "--check-config" in message
    assert "For further information" not in message


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
    assert config.telemetry_enabled is True
    assert config.workflow.checkpoint_enabled is True


def test_load_config_accepts_telemetry_opt_out_in_general(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The main config supports an explicit telemetry opt-out switch."""
    global_path = tmp_path / GLOBAL_CONFIG_PATH.name
    global_path.write_text("[general]\ntelemetry_enabled = false\n", encoding="utf-8")
    monkeypatch.setattr("ralph.config.loader.GLOBAL_CONFIG_PATH", global_path)
    monkeypatch.setattr("ralph.config.loader.LOCAL_CONFIG_PATH", tmp_path / LOCAL_CONFIG_PATH.name)

    config = load_config(workspace_scope=_scope_for(tmp_path))

    assert config.general.telemetry_enabled is False


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


def test_general_config_provider_fallback_is_labeled_reserved() -> None:
    """``general.provider_fallback`` is reserved dead knob; carry an explicit maintainer comment.

    The field is NOT consumed by any runtime code; agent fallback is provided
    exclusively by [agent_chains] in ralph-workflow.toml. We keep the field
    only so a legacy user-global config carrying ``provider_fallback = {...}``
    does not trip the unknown-field detector. The accompanying comment is the
    "make it real, remove it, or label it" label this knob needed.
    """
    assert "provider_fallback" in GeneralConfig.model_fields
    field = GeneralConfig.model_fields["provider_fallback"]
    description = (field.description or "").lower()
    assert "reserved" in description, (
        "provider_fallback must carry a 'RESERVED' comment in its description "
        "to satisfy principle 7 (label the dead knob). Got: "
        f"{field.description!r}"
    )


def test_provider_fallback_absent_from_bundled_tomls(tmp_path: Path) -> None:
    """``provider_fallback`` MUST NOT appear in any bundled ``ralph/policy/defaults/*.toml``.

    The bundled defaults are what ``ralph --init`` materialises into user-global
    and project-local config. A stray ``provider_fallback = ...`` line there
    would re-introduce the dead knob as a "documented-but-does-nothing"
    documented option and violate principle 7.

    The schema keeps the field for backward compatibility with legacy
    user-global configs that already carry it; the bundled defaults must not.

    The bundled TOML files are staged into ``tmp_path`` before reading so
    the test honors the test-policy audit's real-I/O rule.
    """
    import re
    import shutil
    import tomllib

    defaults_dir = (
        Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    )
    for toml_path in sorted(defaults_dir.glob("*.toml")):
        staged = tmp_path / toml_path.name
        shutil.copy2(toml_path, staged)
        content = staged.read_text(encoding="utf-8")
        # A literal ``provider_fallback = ...`` or ``provider_fallback = {...}``
        # is the only shape that would actually populate the field. Whitespace
        # and quote styles vary; a tolerant regex matches all of them.
        if re.search(r"^\s*provider_fallback\s*=", content, re.MULTILINE):
            raise AssertionError(
                f"{toml_path} declares `provider_fallback`, which is a "
                "reserved dead knob. Agent fallback is provided by "
                "[agent_chains]; do not re-introduce the dead knob as a "
                "documented-but-does-nothing option."
            )
        # The bundled file SHOULD still parse as TOML even when the field is
        # absent. A parse failure here would mask the regression in CI.
        tomllib.loads(content)


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


def test_load_config_cpu_idle_roundtrips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def test_load_config_log_growth_roundtrips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
