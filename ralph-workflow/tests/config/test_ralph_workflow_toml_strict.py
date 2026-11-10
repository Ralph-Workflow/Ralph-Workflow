"""Tests for strict error behavior on malformed ralph-workflow.toml files.

ralph-workflow.toml (global, local, propagated) must fail loudly with
ConfigTomlError (What/Why/Fix envelope) on syntax/encoding errors, and SystemExit(1)
on schema validation failures. It never falls back to defaults.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from ralph.config.loader import ConfigTomlError, load_config
from ralph.workspace.scope import WorkspaceScope


def test_global_ralph_workflow_toml_malformed_raises_config_toml_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A malformed global ralph-workflow.toml must raise ConfigTomlError."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    global_path = config_home / "ralph-workflow.toml"
    global_path.write_text("[general\ninvalid syntax = = =\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    local_path = tmp_path / "local-ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")

    with pytest.raises(ConfigTomlError) as exc_info:
        load_config(config_path=local_path)

    msg = str(exc_info.value)
    assert "What failed: Ralph could not read" in msg
    assert str(global_path) in msg
    assert "Why it matters: settings in a malformed file are not safe to use." in msg
    assert f"Fix: correct the TOML syntax in {global_path}, then run `ralph --check-config`." in msg


def test_local_ralph_workflow_toml_malformed_raises_config_toml_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A malformed project-local ralph-workflow.toml must raise ConfigTomlError."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    local_path = tmp_path / "local-ralph-workflow.toml"
    local_path.write_text("[general\nbad = =\n", encoding="utf-8")

    with pytest.raises(ConfigTomlError) as exc_info:
        load_config(config_path=local_path)

    msg = str(exc_info.value)
    assert "What failed: Ralph could not read" in msg
    assert str(local_path) in msg
    assert "Why it matters: settings in a malformed file are not safe to use." in msg
    assert f"Fix: correct the TOML syntax in {local_path}, then run `ralph --check-config`." in msg


def test_propagated_ralph_workflow_toml_malformed_raises_config_toml_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A malformed propagated ancestor ralph-workflow.toml must raise ConfigTomlError."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    parent_dir = tmp_path / "parent"
    parent_dir.mkdir(parents=True)
    propagated_path = parent_dir / "ralph-workflow.toml"
    propagated_path.write_text("[broken toml <<<===\n", encoding="utf-8")

    child_dir = tmp_path / "child"
    child_dir.mkdir(parents=True)
    local_path = child_dir / "ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")

    scope = WorkspaceScope(
        root=child_dir,
        local_config_path=local_path,
        propagated_config_paths=(propagated_path,),
    )

    with pytest.raises(ConfigTomlError) as exc_info:
        load_config(workspace_scope=scope)

    msg = str(exc_info.value)
    assert "What failed: Ralph could not read" in msg
    assert str(propagated_path) in msg
    assert "Why it matters: settings in a malformed file are not safe to use." in msg
    assert (
        f"Fix: correct the TOML syntax in {propagated_path}, then run `ralph --check-config`."
        in msg
    )


def test_ralph_workflow_toml_bad_encoding_raises_config_toml_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ralph-workflow.toml with invalid UTF-8 encoding must raise ConfigTomlError."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    local_path = tmp_path / "local-ralph-workflow.toml"
    local_path.write_bytes(b"\xff\xfe\x00\x00invalid utf8")

    with pytest.raises(ConfigTomlError) as exc_info:
        load_config(config_path=local_path)

    msg = str(exc_info.value)
    assert "What failed: Ralph could not read" in msg
    assert str(local_path) in msg
    assert "Why it matters: settings in a malformed file are not safe to use." in msg
    assert f"Fix: correct the TOML syntax in {local_path}, then run `ralph --check-config`." in msg


def test_schema_invalid_ralph_workflow_toml_fails_loudly_with_system_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A schema-invalid ralph-workflow.toml must fail loudly with SystemExit(1)."""
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    local_path = tmp_path / "local-ralph-workflow.toml"
    local_path.write_text('[general]\nverbosity = "not_an_int"\n', encoding="utf-8")

    records: list[str] = []
    sink_id = logger.add(records.append, level="ERROR", format="{message}")
    try:
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=local_path)
    finally:
        logger.remove(sink_id)

    assert exc_info.value.code == 1
    msg = "\n".join(records)
    assert "What failed:" in msg
    assert "Why it matters:" in msg
    assert "Fix:" in msg
    assert str(local_path) in msg
