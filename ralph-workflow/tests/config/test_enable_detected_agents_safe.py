"""Tests for safe agents TOML write in enable_detected_agents."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

import pytest
from loguru import logger

from ralph.config.agent_detection import enable_detected_agents


def _capture_loguru_records(level: str = "DEBUG") -> tuple[list[tuple[str, int]], int]:
    """Attach a loguru sink that records every emitted message.

    Returns the captured ``(message, level number)`` pairs and the sink id
    so the caller can remove the sink again. loguru is the operator-visible
    sink the CLI wires, so a record captured here is a line the operator
    can actually see.
    """
    records: list[tuple[str, int]] = []

    def sink(message: object) -> None:
        record = getattr(message, "record", None)
        if not isinstance(record, dict):
            return
        text = str(record.get("message", ""))
        number = getattr(record.get("level"), "no", 0)
        records.append((text, number if isinstance(number, int) else 0))

    sink_id = logger.add(sink, level=level)
    return records, sink_id


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
    data = tomllib.loads(new_content)
    assert isinstance(data, dict)
    agents = data.get("agents")
    assert isinstance(agents, dict)
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
    data = tomllib.loads(new_content)
    assert isinstance(data, dict)
    agents = data.get("agents")
    assert isinstance(agents, dict)
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


def test_enable_detected_agents_emits_trigger_line_to_operator_visible_loguru_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The enable-detected path's trigger line reaches the loguru sink the operator sees."""
    config_path = tmp_path / "ralph-workflow-agents.toml"
    config_path.write_text(
        '[agents.claude]\ncmd = "claude"\n\n'
        "# @AGENT-BLOCK-START: codex\n"
        "# [agents.codex]\n"
        '# cmd = "codex exec"\n'
        "# @AGENT-BLOCK-END\n",
        encoding="utf-8",
    )
    bak_path = config_path.with_name(f"{config_path.name}.bak")

    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: ["codex"])

    records, sink_id = _capture_loguru_records()
    try:
        enabled = enable_detected_agents(config_path)
    finally:
        logger.remove(sink_id)

    assert enabled == ["codex"]
    matching = [
        (text, level_no)
        for text, level_no in records
        if str(config_path) in text
        and "ralph --init: enable detected agents" in text
        and (str(bak_path) in text or bak_path.name in text)
    ]
    assert matching, (
        "no operator-visible trigger line for the enable-detected path; captured: "
        f"{[text for text, _ in records]}"
    )
    # The line is emitted at INFO or above, so it is visible without debug verbosity.
    assert max(level_no for _, level_no in matching) >= 20


CONFLICTING_AGENTS_FILE = (
    "# Operator header\n"
    "[agents.claude]\n"
    'cmd = "claude"\n'
    'operator_custom_key = "custom_value"\n\n'
    "# @AGENT-BLOCK-START: claude\n"
    "# [agents.claude]\n"
    '# cmd = "claude"\n'
    "# @AGENT-BLOCK-END\n\n"
    "# @AGENT-BLOCK-START: codex\n"
    "# [agents.codex]\n"
    '# cmd = "codex exec"\n'
    "# @AGENT-BLOCK-END\n"
)


def _skip_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the warning messages that name the enable-detected trigger."""
    return [
        record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and "ralph --init: enable detected agents" in record.message
    ]


def test_enable_detected_agents_skips_block_conflicting_with_existing_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A block that would duplicate an existing table is skipped; the file stays byte-identical."""
    config_path = tmp_path / "ralph-workflow-agents.toml"
    config_path.write_text(CONFLICTING_AGENTS_FILE, encoding="utf-8")
    original_bytes = config_path.read_bytes()
    bak_path = config_path.with_name(f"{config_path.name}.bak")

    monkeypatch.setattr("ralph.config.agent_detection.detect_installed_agents", lambda: ["claude"])

    with caplog.at_level(logging.WARNING):
        enabled = enable_detected_agents(config_path)

    # The conflicting agent is NOT enabled and nothing is written.
    assert enabled == []
    assert config_path.read_bytes() == original_bytes
    assert not bak_path.exists()

    # The warning names the agent and the trigger.
    skip_warnings = _skip_warnings(caplog)
    assert len(skip_warnings) == 1
    assert "claude" in skip_warnings[0]

    # The operator's data survives and the file still parses as TOML.
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    agents = data.get("agents", {})
    assert isinstance(agents, dict)
    assert agents["claude"]["operator_custom_key"] == "custom_value"


def test_enable_detected_agents_applies_non_conflicting_block_beside_a_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only the conflicting block is skipped; a valid block in the same file is enabled."""
    config_path = tmp_path / "ralph-workflow-agents.toml"
    config_path.write_text(CONFLICTING_AGENTS_FILE, encoding="utf-8")
    original_bytes = config_path.read_bytes()
    bak_path = config_path.with_name(f"{config_path.name}.bak")

    monkeypatch.setattr(
        "ralph.config.agent_detection.detect_installed_agents", lambda: ["claude", "codex"]
    )

    with caplog.at_level(logging.WARNING):
        enabled = enable_detected_agents(config_path)

    # The conflicting agent is skipped, the valid one is enabled.
    assert enabled == ["codex"]

    new_text = config_path.read_text(encoding="utf-8")
    data = tomllib.loads(new_text)
    agents = data.get("agents", {})
    assert isinstance(agents, dict)
    assert agents["codex"]["cmd"] == "codex exec"

    # The operator's table and the skipped block are preserved verbatim.
    assert '[agents.claude]\ncmd = "claude"\noperator_custom_key = "custom_value"\n' in new_text
    assert "# @AGENT-BLOCK-START: claude\n# [agents.claude]\n" in new_text

    # The backup holds the operator's previous bytes.
    assert bak_path.read_bytes() == original_bytes

    # Only the conflicting agent is reported as skipped.
    skip_warnings = _skip_warnings(caplog)
    assert len(skip_warnings) == 1
    assert "claude" in skip_warnings[0]
    assert "codex" not in skip_warnings[0]


def test_enable_detected_agents_does_not_raise_on_a_conflicting_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A table the operator spelled differently still conflicts; the write never raises."""
    config_path = tmp_path / "ralph-workflow-agents.toml"
    initial_content = (
        '[agents."claude"]\n'
        'cmd = "claude"\n'
        'operator_custom_key = "custom_value"\n\n'
        "# @AGENT-BLOCK-START: claude\n"
        "# [agents.claude]\n"
        '# cmd = "claude"\n'
        "# @AGENT-BLOCK-END\n\n"
        "# @AGENT-BLOCK-START: codex\n"
        "# [agents.codex]\n"
        '# cmd = "codex exec"\n'
        "# @AGENT-BLOCK-END\n"
    )
    config_path.write_text(initial_content, encoding="utf-8")

    monkeypatch.setattr(
        "ralph.config.agent_detection.detect_installed_agents", lambda: ["claude", "codex"]
    )

    with caplog.at_level(logging.WARNING):
        enabled = enable_detected_agents(config_path)

    assert enabled == ["codex"]

    new_text = config_path.read_text(encoding="utf-8")
    data = tomllib.loads(new_text)
    agents = data.get("agents", {})
    assert isinstance(agents, dict)
    assert agents["claude"]["operator_custom_key"] == "custom_value"
    assert agents["codex"]["cmd"] == "codex exec"

    skip_warnings = _skip_warnings(caplog)
    assert len(skip_warnings) == 1
    assert "claude" in skip_warnings[0]


def test_enable_detected_agents_on_invalid_utf8_file_reports_and_keeps_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An agents file that is not valid UTF-8 is reported, never crashed on."""
    config_path = tmp_path / """ralph-workflow-agents.toml"""
    original_bytes = b"""[agents.claude]\ncmd = "\xff\xfe"\n"""
    config_path.write_bytes(original_bytes)

    monkeypatch.setattr(
        """ralph.config.agent_detection.detect_installed_agents""", lambda: ["""codex"""]
    )

    with caplog.at_level(logging.WARNING):
        enabled = enable_detected_agents(config_path)

    assert enabled == []
    assert config_path.read_bytes() == original_bytes
    assert """ralph --regenerate-config""" in caplog.text
