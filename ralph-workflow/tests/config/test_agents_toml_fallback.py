"""Tests for fallback behavior of agents configuration files.

When ~/.config/ralph-workflow-agents.toml is unreadable (invalid TOML, bad encoding,
permission error, or schema-invalid), Ralph must fall back to built-in defaults,
leave the file on disk untouched, and emit a deduped loguru WARNING mentioning the
file, error, default in use, and `ralph --check-config`.
When strict_optional_files=True is passed, errors are re-raised.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from loguru import logger
from pydantic import ValidationError

from ralph.config._toml_fallback import reset_fallback_warning_registry
from ralph.config.loader import load_config


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    reset_fallback_warning_registry()


def _capture_warnings() -> tuple[list[str], int]:
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    return records, sink_id


def test_agents_toml_invalid_toml_fallback_and_dedup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Invalid TOML falls back to default, leaves file unchanged, and warns with dedup."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    agents_path = config_home / "ralph-workflow-agents.toml"
    invalid_content = "[agents\ninvalid toml syntax = = =\n"
    agents_path.write_text(invalid_content, encoding="utf-8")
    before_bytes = agents_path.read_bytes()

    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    # 1. First load: falls back to default, emits WARN
    records, sink_id = _capture_warnings()
    try:
        config = load_config(config_path=local_path)
    finally:
        logger.remove(sink_id)

    assert config.agents == {}
    assert agents_path.read_bytes() == before_bytes  # file bytes untouched
    warning_text = "\n".join(records)
    assert str(agents_path) in warning_text
    assert "agents" in warning_text
    assert "using built-in default" in warning_text
    assert "ralph --check-config" in warning_text
    first_warning_count = len([r for r in records if "using built-in default" in r])
    assert first_warning_count == 1

    # 2. Second load: deduped, no duplicate WARN
    records2, sink_id2 = _capture_warnings()
    try:
        config2 = load_config(config_path=local_path)
    finally:
        logger.remove(sink_id2)

    assert config2.agents == {}
    second_warning_count = len([r for r in records2 if "using built-in default" in r])
    assert second_warning_count == 0

    # 3. Content change: re-warns
    changed_content = "[agents\ndifferent invalid toml @@@\n"
    agents_path.write_text(changed_content, encoding="utf-8")
    changed_bytes = agents_path.read_bytes()

    records3, sink_id3 = _capture_warnings()
    try:
        config3 = load_config(config_path=local_path)
    finally:
        logger.remove(sink_id3)

    assert config3.agents == {}
    assert agents_path.read_bytes() == changed_bytes
    re_warn_count = len([r for r in records3 if "using built-in default" in r])
    assert re_warn_count == 1


def test_agents_toml_bad_encoding_fallback_and_dedup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bad encoding falls back to default, leaves file unchanged, and warns with dedup."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    agents_path = config_home / "ralph-workflow-agents.toml"
    bad_bytes = b"\xff\xfe\x00\x00not utf-8 content"
    agents_path.write_bytes(bad_bytes)

    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    records, sink_id = _capture_warnings()
    try:
        config = load_config(config_path=local_path)
    finally:
        logger.remove(sink_id)

    assert config.agents == {}
    assert agents_path.read_bytes() == bad_bytes
    warning_text = "\n".join(records)
    assert str(agents_path) in warning_text
    assert "using built-in default" in warning_text
    assert "ralph --check-config" in warning_text

    # Second load: deduped
    records2, sink_id2 = _capture_warnings()
    try:
        load_config(config_path=local_path)
    finally:
        logger.remove(sink_id2)
    assert len([r for r in records2 if "using built-in default" in r]) == 0

    # Content change: re-warns
    changed_bytes = b"\xfe\xff\x01\x02different bad bytes"
    agents_path.write_bytes(changed_bytes)

    records3, sink_id3 = _capture_warnings()
    try:
        load_config(config_path=local_path)
    finally:
        logger.remove(sink_id3)
    assert len([r for r in records3 if "using built-in default" in r]) == 1


def test_agents_toml_chmod_000_fallback_and_dedup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unreadable (chmod 000) agents file falls back to default and warns."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    agents_path = config_home / "ralph-workflow-agents.toml"
    agents_path.write_text("[agents.claude]\ncmd = 'claude'\n", encoding="utf-8")
    agents_path.chmod(0o000)

    try:
        try:
            agents_path.read_bytes()
            pytest.skip("Running as root; chmod 000 does not prevent reading.")
        except PermissionError:
            pass

        local_path = tmp_path / "ralph-workflow.toml"
        local_path.write_text("", encoding="utf-8")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

        records, sink_id = _capture_warnings()
        try:
            config = load_config(config_path=local_path)
        finally:
            logger.remove(sink_id)

        assert config.agents == {}
        warning_text = "\n".join(records)
        assert str(agents_path) in warning_text
        assert "using built-in default" in warning_text
        assert "ralph --check-config" in warning_text

        # Second load: deduped
        records2, sink_id2 = _capture_warnings()
        try:
            load_config(config_path=local_path)
        finally:
            logger.remove(sink_id2)
        assert len([r for r in records2 if "using built-in default" in r]) == 0
    finally:
        agents_path.chmod(0o644)


def test_agents_toml_schema_invalid_fallback_and_dedup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Schema-invalid agents file falls back to default, leaves file unchanged, and warns with dedup."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    agents_path = config_home / "ralph-workflow-agents.toml"
    invalid_schema = "[agents.broken]\ncmd = 12345\n"
    agents_path.write_text(invalid_schema, encoding="utf-8")
    before_bytes = agents_path.read_bytes()

    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    records, sink_id = _capture_warnings()
    try:
        config = load_config(config_path=local_path)
    finally:
        logger.remove(sink_id)

    assert config.agents == {}
    assert agents_path.read_bytes() == before_bytes
    warning_text = "\n".join(records)
    assert str(agents_path) in warning_text
    assert "using built-in default" in warning_text
    assert "ralph --check-config" in warning_text

    # Second load: deduped
    records2, sink_id2 = _capture_warnings()
    try:
        load_config(config_path=local_path)
    finally:
        logger.remove(sink_id2)
    assert len([r for r in records2 if "using built-in default" in r]) == 0

    # Content change: re-warns
    changed_schema = '[agents.another_broken]\ncmd = "x"\njson_parser = "not_valid"\n'
    agents_path.write_text(changed_schema, encoding="utf-8")
    changed_bytes = agents_path.read_bytes()

    records3, sink_id3 = _capture_warnings()
    try:
        load_config(config_path=local_path)
    finally:
        logger.remove(sink_id3)
    assert len([r for r in records3 if "using built-in default" in r]) == 1
    assert agents_path.read_bytes() == changed_bytes


def test_agents_toml_strict_optional_files_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When strict_optional_files=True, errors in agents toml are re-raised."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    agents_path = config_home / "ralph-workflow-agents.toml"
    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    # Invalid TOML
    agents_path.write_text("[agents\ninvalid toml = =\n", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_config(config_path=local_path, strict_optional_files=True)

    # Bad encoding
    agents_path.write_bytes(b"\xff\xfe\x00\x00bad encoding")
    with pytest.raises(UnicodeDecodeError):
        load_config(config_path=local_path, strict_optional_files=True)

    # Schema invalid
    agents_path.write_text("[agents.broken]\ncmd = 12345\n", encoding="utf-8")
    with pytest.raises((ValidationError, SystemExit)):
        load_config(config_path=local_path, strict_optional_files=True)


def test_agents_toml_valid_loads_successfully_without_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A valid agents file is loaded cleanly without any fallback warnings."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    agents_path = config_home / "ralph-workflow-agents.toml"
    agents_path.write_text('[agents.custom]\ncmd = "custom-agent"\n', encoding="utf-8")

    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    records, sink_id = _capture_warnings()
    try:
        config = load_config(config_path=local_path)
    finally:
        logger.remove(sink_id)

    assert "custom" in config.agents
    assert config.agents["custom"].cmd == "custom-agent"
    assert len([r for r in records if "using built-in default" in r]) == 0
