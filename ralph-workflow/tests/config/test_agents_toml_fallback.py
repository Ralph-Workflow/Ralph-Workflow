from __future__ import annotations

import io
import tomllib
from pathlib import Path

import pytest
from loguru import logger
from pydantic import ValidationError

from ralph.config import reset_fallback_warning_registry
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
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    agents_path = config_home / "ralph-workflow-agents.toml"
    invalid_content = "[agents\ninvalid toml syntax = = =\n"
    agents_path.write_text(invalid_content, encoding="utf-8")
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
    assert "agents" in warning_text
    assert "using built-in default" in warning_text
    assert "ralph --check-config" in warning_text
    assert "ralph --regenerate-config" in warning_text
    assert "line" in warning_text.lower()
    assert "column" in warning_text.lower()
    first_warning_count = len([r for r in records if "using built-in default" in r])
    assert first_warning_count == 1

    records2, sink_id2 = _capture_warnings()
    try:
        config2 = load_config(config_path=local_path)
    finally:
        logger.remove(sink_id2)

    assert config2.agents == {}
    second_warning_count = len([r for r in records2 if "using built-in default" in r])
    assert second_warning_count == 0

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
    assert "ralph --regenerate-config" in warning_text

    records2, sink_id2 = _capture_warnings()
    try:
        load_config(config_path=local_path)
    finally:
        logger.remove(sink_id2)
    assert len([r for r in records2 if "using built-in default" in r]) == 0

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
        assert "ralph --regenerate-config" in warning_text

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
    assert "ralph --regenerate-config" in warning_text

    records2, sink_id2 = _capture_warnings()
    try:
        load_config(config_path=local_path)
    finally:
        logger.remove(sink_id2)
    assert len([r for r in records2 if "using built-in default" in r]) == 0

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
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    agents_path = config_home / "ralph-workflow-agents.toml"
    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    agents_path.write_text("[agents\ninvalid toml = =\n", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_config(config_path=local_path, strict_optional_files=True)

    agents_path.write_bytes(b"\xff\xfe\x00\x00bad encoding")
    with pytest.raises(UnicodeDecodeError):
        load_config(config_path=local_path, strict_optional_files=True)

    agents_path.write_text("[agents.broken]\ncmd = 12345\n", encoding="utf-8")
    with pytest.raises((ValidationError, SystemExit)):
        load_config(config_path=local_path, strict_optional_files=True)


def test_agents_toml_valid_loads_successfully_without_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_corrupt_agents_toml_load_config_and_diagnose_contain_path_line_col_and_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    agents_path = config_home / "ralph-workflow-agents.toml"
    invalid_content = "[agents]\ninvalid = = =\n"
    agents_path.write_text(invalid_content, encoding="utf-8")

    local_path = tmp_path / "ralph-workflow.toml"
    local_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    records, sink_id = _capture_warnings()
    try:
        cfg = load_config(config_path=local_path)
    finally:
        logger.remove(sink_id)

    assert cfg.agents == {}
    warn = "\n".join(records)
    assert str(agents_path) in warn
    assert "line 2" in warn
    assert "column" in warn
    assert "ralph --regenerate-config" in warn
    assert "using built-in default" in warn

    reset_fallback_warning_registry()
    from ralph.config.bootstrap import collect_unreadable_config_files

    records_diag, sink_id_diag = _capture_warnings()
    try:
        findings = collect_unreadable_config_files(
            config_path=local_path,
            workspace_scope=None,
        )
    finally:
        logger.remove(sink_id_diag)

    assert len(findings) == 1
    assert findings[0].path == agents_path
    assert findings[0].kind == "agents"
    diag_warn = "\n".join(records_diag)
    assert str(agents_path) in diag_warn
    assert "line 2" in diag_warn
    assert "column" in diag_warn
    assert "ralph --regenerate-config" in diag_warn


def test_corrupt_agents_toml_console_warning_names_regenerate_config(
    tmp_path: Path, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_git_repo)
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    agents_path = config_home / "ralph-workflow-agents.toml"
    agents_path.write_text("[agents]\nbad syntax = = =\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    agent_dir = tmp_git_repo / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "ralph-workflow.toml").write_text("[general]\n", encoding="utf-8")

    from rich.console import Console

    from ralph.cli.commands.diagnose import diagnose_command
    from ralph.display.context import make_display_context

    console_buf = io.StringIO()
    console = Console(file=console_buf, force_terminal=False, width=200)
    display_ctx = make_display_context(console=console)

    records, sink_id = _capture_warnings()
    try:
        exit_code = diagnose_command(
            config_path=None,
            cli_overrides={},
            display_context=display_ctx,
        )
    finally:
        logger.remove(sink_id)

    assert exit_code != 0
    rendered = " ".join(console_buf.getvalue().split())
    assert "ralph --regenerate-config" in rendered, (
        "Expected the console warning for the unreadable agents file to name the "
        f"recovery command:\n{console_buf.getvalue()}"
    )

    load_records = [record for record in records if "Failed to load agents configuration" in record]
    assert load_records, f"expected a loguru warning for the agents file, got: {records}"
    loguru_hint = load_records[0].split("using built-in default. ", 1)[-1].strip()
    assert "ralph --regenerate-config" in loguru_hint
    assert loguru_hint in rendered, (
        f"expected the console hint to match the loguru hint {loguru_hint!r}:\n"
        f"{console_buf.getvalue()}"
    )


def test_corrupt_agents_toml_diagnose_command_warns_with_recovery_instructions(
    tmp_path: Path, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_git_repo)
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    agents_path = config_home / "ralph-workflow-agents.toml"
    agents_path.write_text("[agents]\nbad syntax = = =\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    agent_dir = tmp_git_repo / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "ralph-workflow.toml").write_text("[general]\n", encoding="utf-8")

    from rich.console import Console

    from ralph.cli.commands.diagnose import diagnose_command
    from ralph.display.context import make_display_context

    console = Console(file=io.StringIO(), force_terminal=False)
    display_ctx = make_display_context(console=console)

    records, sink_id = _capture_warnings()
    try:
        exit_code = diagnose_command(
            config_path=None,
            cli_overrides={},
            display_context=display_ctx,
        )
    finally:
        logger.remove(sink_id)

    assert exit_code != 0
    warn = "\n".join(records)
    assert str(agents_path) in warn
    assert "line 2" in warn
    assert "column" in warn
    assert "ralph --regenerate-config" in warn
