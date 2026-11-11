"""Unit tests for the safe agents TOML writer."""

from __future__ import annotations

import ast
import logging
import os
import tempfile
from pathlib import Path

import pytest

from ralph.config.agents_file_writer import write_agents_toml


def test_invalid_toml_raises_value_error_and_does_not_create_file(
    tmp_path: Path,
) -> None:
    """When text is not valid TOML, ValueError is raised and no file is created."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    invalid_toml = "agents: invalid_syntax = [unclosed"

    with pytest.raises(ValueError, match="Invalid TOML"):
        write_agents_toml(target_path, invalid_toml, trigger="test_invalid")

    assert not target_path.exists()
    assert not target_path.with_name(f"{target_path.name}.bak").exists()


def test_invalid_toml_leaves_existing_file_untouched(tmp_path: Path) -> None:
    """When text is not valid TOML, any existing file and backup are untouched."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    initial_content = '[agents.claude]\nmodel = "claude-3"\n'
    target_path.write_text(initial_content, encoding="utf-8")

    invalid_toml = "[unclosed_table"
    with pytest.raises(ValueError, match="Invalid TOML"):
        write_agents_toml(target_path, invalid_toml, trigger="test_invalid_existing")

    assert target_path.read_text(encoding="utf-8") == initial_content
    assert not target_path.with_name(f"{target_path.name}.bak").exists()


def test_write_creates_new_file_and_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fresh target file is written atomically and logged with trigger and .bak path."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    content = '[agents.claude]\nmodel = "claude-3-5-sonnet"\n'
    bak_path = target_path.with_name(f"{target_path.name}.bak")

    with caplog.at_level(logging.INFO):
        written = write_agents_toml(target_path, content, trigger="initial_creation")

    assert written is True
    assert target_path.exists()
    assert target_path.read_text(encoding="utf-8") == content
    assert not bak_path.exists()

    # Log must include path, trigger, and .bak path
    assert str(target_path) in caplog.text
    assert "initial_creation" in caplog.text
    assert str(bak_path) in caplog.text


def test_write_creates_backup_when_content_differs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When existing file differs, a .bak copy of original is made before writing."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    bak_path = target_path.with_name(f"{target_path.name}.bak")
    old_content = '[agents.claude]\nmodel = "old-model"\n'
    new_content = '[agents.claude]\nmodel = "new-model"\n'

    target_path.write_text(old_content, encoding="utf-8")

    with caplog.at_level(logging.INFO):
        written = write_agents_toml(target_path, new_content, trigger="migration_test")

    assert written is True
    assert target_path.read_text(encoding="utf-8") == new_content
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == old_content

    assert str(target_path) in caplog.text
    assert "migration_test" in caplog.text
    assert str(bak_path) in caplog.text


def test_write_identical_content_returns_false_and_no_backup_or_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Identical content returns False immediately without creating a backup or logging."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    bak_path = target_path.with_name(f"{target_path.name}.bak")
    content = '[agents.claude]\nmodel = "same-model"\n'

    target_path.write_text(content, encoding="utf-8")

    with caplog.at_level(logging.INFO):
        written = write_agents_toml(target_path, content, trigger="identical_content")

    assert written is False
    assert target_path.read_text(encoding="utf-8") == content
    assert not bak_path.exists()
    assert caplog.text == ""


def test_write_atomic_cleanup_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unique temp file is cleaned up if os.replace fails."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    content = '[agents.claude]\nmodel = "temp-test"\n'

    recorded_tmp_paths: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def tracking_mkstemp(
        suffix: str | None = None,
        prefix: str | None = None,
        dir: str | os.PathLike[str] | None = None,
        text: bool = False,
    ) -> tuple[int, str]:
        fd, path_str = real_mkstemp(suffix=suffix, prefix=prefix, dir=dir, text=text)
        recorded_tmp_paths.append(Path(path_str))
        return fd, path_str

    monkeypatch.setattr(tempfile, "mkstemp", tracking_mkstemp)

    def failing_replace(
        src: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        dst: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        raise OSError("Simulated replace failure")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="Simulated replace failure"):
        write_agents_toml(target_path, content, trigger="failure_test")

    assert not target_path.exists()
    assert len(recorded_tmp_paths) == 1
    tmp_file = recorded_tmp_paths[0]
    assert tmp_file.name.startswith(f".{target_path.name}.tmp.")
    assert tmp_file.parent == target_path.parent
    assert not tmp_file.exists()


def test_circular_import_and_stdlib_only_constraint() -> None:
    """Module must import only standard library and no internal ralph modules."""
    import ralph.config.agents_file_writer as writer_mod

    assert writer_mod.__file__ is not None
    source_path = Path(writer_mod.__file__)
    parsed = ast.parse(source_path.read_text(encoding="utf-8"))

    allowed_top_levels = {
        "__future__",
        "logging",
        "os",
        "pathlib",
        "tempfile",
        "tomllib",
    }

    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                assert top_level in allowed_top_levels, (
                    f"Unexpected import: {alias.name} (top-level: {top_level})"
                )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            top_level = node.module.split(".")[0]
            assert top_level in allowed_top_levels, (
                f"Unexpected from-import: {node.module} (top-level: {top_level})"
            )
