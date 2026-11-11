"""Tests for safe agents TOML write in enable_detected_agents."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import cast

import pytest

from ralph.config.agent_detection import enable_detected_agents


def test_enable_detected_agents_creates_backup_preserving_operator_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operator edits in the file are preserved in a .bak file and the write is logged."""
    config_path = tmp_path / "ralph-workflow-agents.toml"
    initial_content = (
        "# Custom operator header\n"
        "[agents.claude]\n"
        'cmd = "claude"\n'
        'operator_custom_key = "custom_value"\n\n'
        "# @AGENT-BLOCK-START: codex\n"
        "# [agents.codex]\n"
        '# cmd = "codex exec"\n'
        "# @AGENT-BLOCK-END\n"
    )
    config_path.write_text(initial_content, encoding="utf-8")
    bak_path = config_path.with_name(f"{config_path.name}.bak")

    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: ["codex"])

    with caplog.at_level(logging.INFO):
        enabled = enable_detected_agents(config_path)

    assert enabled == ["codex"]
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == initial_content

    # The updated file has the activated block and operator edit intact
    new_content = config_path.read_text(encoding="utf-8")
    assert 'operator_custom_key = "custom_value"' in new_content
    assert "[agents.codex]" in new_content
    data = cast("dict[str, dict[str, object]]", tomllib.loads(new_content))
    agents = data.get("agents", {})
    assert "codex" in agents
    assert "claude" in agents

    # Log output names trigger and .bak path
    assert "ralph --init: enable detected agents" in caplog.text
    assert str(bak_path) in caplog.text
    assert str(config_path) in caplog.text


def test_enable_detected_agents_skips_invalid_toml_block_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An agent block with invalid TOML syntax is skipped with a warning, keeping remaining valid blocks."""
    config_path = tmp_path / "ralph-workflow-agents.toml"
    initial_content = (
        "[agents.claude]\n"
        'cmd = "claude"\n\n'
        "# @AGENT-BLOCK-START: codex\n"
        "# [agents.codex\n"
        "# cmd = invalid_syntax = [unclosed\n"
        "# @AGENT-BLOCK-END\n\n"
        "# @AGENT-BLOCK-START: pi\n"
        "# [agents.pi]\n"
        '# cmd = "pi"\n'
        "# @AGENT-BLOCK-END\n"
    )
    config_path.write_text(initial_content, encoding="utf-8")

    monkeypatch.setattr(
        "ralph.config.agent_detection.detect_installed_agents", lambda: ["codex", "pi"]
    )

    with caplog.at_level(logging.WARNING):
        enabled = enable_detected_agents(config_path)

    # codex had invalid syntax, so only pi was enabled
    assert enabled == ["pi"]

    # Warning logged for codex
    assert any(
        "codex" in record.message for record in caplog.records if record.levelno >= logging.WARNING
    )

    # Resulting file is valid TOML
    new_content = config_path.read_text(encoding="utf-8")
    data = cast("dict[str, dict[str, object]]", tomllib.loads(new_content))
    agents = data.get("agents", {})
    assert "pi" in agents
    assert "codex" not in agents


def test_enable_detected_agents_all_invalid_produces_no_backup_and_no_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When all detected blocks have invalid syntax, nothing is enabled, no backup is made, and no write occurs."""
    config_path = tmp_path / "ralph-workflow-agents.toml"
    initial_content = (
        "[agents.claude]\n"
        'cmd = "claude"\n\n'
        "# @AGENT-BLOCK-START: codex\n"
        "# [agents.codex\n"
        "# invalid_syntax ===\n"
        "# @AGENT-BLOCK-END\n"
    )
    config_path.write_text(initial_content, encoding="utf-8")
    bak_path = config_path.with_name(f"{config_path.name}.bak")

    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: ["codex"])

    with caplog.at_level(logging.WARNING):
        enabled = enable_detected_agents(config_path)

    assert enabled == []
    assert not bak_path.exists()
    assert config_path.read_text(encoding="utf-8") == initial_content
    assert any(
        "codex" in record.message for record in caplog.records if record.levelno >= logging.WARNING
    )


def test_enable_detected_agents_unchanged_content_produces_no_backup_and_no_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When agents are already enabled or none are detected, no backup is made and no write occurs."""
    config_path = tmp_path / "ralph-workflow-agents.toml"
    initial_content = '[agents.claude]\ncmd = "claude"\n\n[agents.codex]\ncmd = "codex exec"\n'
    config_path.write_text(initial_content, encoding="utf-8")
    bak_path = config_path.with_name(f"{config_path.name}.bak")

    monkeypatch.setattr(
        "ralph.config.agent_detection.detect_installed_agents", lambda: ["claude", "codex"]
    )

    with caplog.at_level(logging.INFO):
        enabled = enable_detected_agents(config_path)

    assert enabled == []
    assert not bak_path.exists()
    assert config_path.read_text(encoding="utf-8") == initial_content
    # write_agents_toml should not have logged a write
    assert "Wrote" not in caplog.text
