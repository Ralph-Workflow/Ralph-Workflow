"""Tests for retired auto-integration configuration keys."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ralph.config.general_config import GeneralConfig
from ralph.config.loader import load_config, load_local_only


def test_retired_auto_integrate_key_is_ignored_once_without_unknown_warning(tmp_path: Path) -> None:
    """S-1: a retired key is ignored and warned once, never reported as unknown."""
    path = tmp_path / "ralph-workflow.toml"
    path.write_text("[general]\nauto_integrate_remote_sync_enabled = true\n", encoding="utf-8")
    records: list[str] = []
    sink = logger.add(records.append, level="WARNING", format="{message}")
    try:
        config = load_local_only(path).general
    finally:
        logger.remove(sink)

    assert config == GeneralConfig()
    warnings = [line for line in records if "auto_integrate_remote_sync_enabled" in line]
    assert len(warnings) == 1
    assert "ignored" in warnings[0]
    assert not any("unknown setting" in line for line in records)


def test_retired_auto_integrate_key_warns_once_per_source_layer(
    monkeypatch, tmp_path: Path
) -> None:
    """S-2: matching retired keys in global and local layers warn once each."""
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "ralph-workflow.toml").write_text(
        "[general]\nauto_integrate_push_enabled = true\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    local = tmp_path / "local.toml"
    local.write_text("[general]\nauto_integrate_push_enabled = false\n", encoding="utf-8")
    records: list[str] = []
    sink = logger.add(records.append, level="WARNING", format="{message}")
    try:
        config = load_config(config_path=local).general
    finally:
        logger.remove(sink)

    assert config == GeneralConfig()
    warnings = [line for line in records if "auto_integrate_push_enabled" in line]
    assert len(warnings) == 2
    assert all("ignored" in line for line in warnings)
    assert not any("unknown setting" in line for line in records)
