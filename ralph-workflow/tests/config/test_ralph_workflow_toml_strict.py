from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from ralph.config.loader import ConfigTomlError, load_config
from ralph.workspace.scope import WorkspaceScope


def test_global_ralph_workflow_toml_malformed_raises_config_toml_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_unreadable_config_finding_strict_file_fails_loudly_never_defaults(
    tmp_path: Path,
) -> None:
    from unittest.mock import MagicMock

    from ralph.config import reset_fallback_warning_registry
    from ralph.config.unreadable_config_finding import collect_unreadable_config_files

    reset_fallback_warning_registry()
    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("[general\nbroken syntax ===\n", encoding="utf-8")

    mock_display = MagicMock()
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        findings = collect_unreadable_config_files(
            config_path=local_path,
            display=mock_display,
        )
    finally:
        logger.remove(sink_id)

    assert len(findings) == 1
    assert findings[0].path == local_path
    assert findings[0].kind == "ralph-workflow"

    warn_text = "\n".join(records)
    assert "strict configuration behaviour in effect" in warn_text
    assert "(this file has no built-in default fallback)" in warn_text
    assert "using built-in default" not in warn_text

    mock_display.emit_warning.assert_called_once()
    display_msg = str(mock_display.emit_warning.call_args[0][0])
    assert "strict configuration behaviour in effect" in display_msg
    assert "using built-in default" not in display_msg

    with pytest.raises(ConfigTomlError) as exc_info:
        load_config(config_path=local_path)
    assert "What failed: Ralph could not read" in str(exc_info.value)
