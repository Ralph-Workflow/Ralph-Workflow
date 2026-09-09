from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from ralph.config.loader import ConfigTomlError, load_config, load_toml


def test_load_config_removes_retired_agent_key_from_explicit_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        "# Keep this operator note.\n"
        "[agents.claude]\n"
        'cmd = "claude"\n'
        "can_commit = true\n"
        'display_name = "Claude Code"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))

    config = load_config(config_path=config_path)

    rewritten = config_path.read_text(encoding="utf-8")
    assert config.agents["claude"].display_name == "Claude Code"
    assert "# Keep this operator note." in rewritten
    assert 'cmd = "claude"' in rewritten
    assert 'display_name = "Claude Code"' in rewritten
    assert "can_commit = true" not in rewritten


def test_load_config_rejects_near_miss_agent_key_without_rewriting_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "ralph-workflow.toml"
    original = '[agents.claude]\ncmd = "claude"\ncan_commmit = true\n'
    config_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    records: list[str] = []
    sink_id = logger.add(records.append, level="ERROR", format="{message}")
    try:
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=config_path)
    finally:
        logger.remove(sink_id)

    assert exc_info.value.code == 1
    assert config_path.read_text(encoding="utf-8") == original
    assert "agents.claude.can_commmit" in "\n".join(records)


def test_load_config_migrates_retired_agent_key_from_global_agents_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_home = tmp_path / "xdg-config"
    config_home.mkdir()
    agents_path = config_home / "ralph-workflow-agents.toml"
    agents_path.write_text(
        '# Keep this agent note.\n[agents."claude"]\ncmd = "claude"\ncan_commit = true\n',
        encoding="utf-8",
    )
    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    config = load_config(config_path=local_path)

    assert config.agents["claude"].cmd == "claude"
    assert agents_path.read_text(encoding="utf-8") == (
        '# Keep this agent note.\n[agents."claude"]\ncmd = "claude"\n'
    )


def test_load_toml_preserves_comments_and_strings_during_retired_agent_key_migration(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        "[agents.claude] # active agent\n"
        'note = "can_commit = true"\n'
        "# can_commit = true\n"
        "can_commit = true # retired\n"
        "[general]\n"
        "can_commit = true\n",
        encoding="utf-8",
    )

    data = load_toml(config_path)

    assert data["agents"] == {"claude": {"note": "can_commit = true"}}
    assert config_path.read_text(encoding="utf-8") == (
        "[agents.claude] # active agent\n"
        'note = "can_commit = true"\n'
        "# can_commit = true\n"
        "[general]\n"
        "can_commit = true\n"
    )


def test_load_toml_does_not_rewrite_malformed_retired_agent_source(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-workflow.toml"
    original = "[agents.claude]\ncan_commit = true\n[general\n"
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigTomlError):
        load_toml(config_path)

    assert config_path.read_text(encoding="utf-8") == original
