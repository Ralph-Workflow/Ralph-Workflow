"""Unit tests for the safe agents TOML writer."""

from __future__ import annotations

import ast
import logging
import os
import stat
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

from ralph.config.agents_file_writer import write_agents_toml


@contextmanager
def _capture_loguru_lines() -> Iterator[list[str]]:
    """Capture the loguru records emitted inside the block."""
    lines: list[str] = []
    sink_id = loguru_logger.add(lines.append, level="INFO", format="{message}")
    try:
        yield lines
    finally:
        loguru_logger.remove(sink_id)


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


def test_write_recovers_existing_file_with_invalid_utf8(tmp_path: Path) -> None:
    """A file with invalid UTF-8 counts as different and its raw bytes are backed up."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    original_bytes = b'[agents.claude]\nmodel = "\xff\xfe-broken"\n'
    target_path.write_bytes(original_bytes)
    new_content = '[agents.claude]\nmodel = "recovered"\n'

    written = write_agents_toml(target_path, new_content, trigger="invalid_utf8_recovery")

    assert written is True
    assert tomllib.loads(target_path.read_text(encoding="utf-8")) == {
        "agents": {"claude": {"model": "recovered"}}
    }
    bak_path = target_path.with_name(f"{target_path.name}.bak")
    assert bak_path.read_bytes() == original_bytes


def test_write_preserves_symlinked_target(tmp_path: Path) -> None:
    """Writing through a symlink updates the real file and keeps the symlink a symlink."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_file = real_dir / "agents.toml"
    original_bytes = b'[agents.claude]\nmodel = "old"\n'
    real_file.write_bytes(original_bytes)

    link_path = tmp_path / "ralph-workflow-agents.toml"
    link_path.symlink_to(real_file)

    new_content = '[agents.claude]\nmodel = "new"\n'
    written = write_agents_toml(link_path, new_content, trigger="symlink_target")

    assert written is True
    assert link_path.is_symlink()
    assert real_file.read_text(encoding="utf-8") == new_content
    bak_path = link_path.with_name(f"{link_path.name}.bak")
    assert bak_path.read_bytes() == original_bytes


def test_write_preserves_existing_file_mode(tmp_path: Path) -> None:
    """Rewriting an existing file keeps its 0644 mode."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    target_path.write_text('[agents.claude]\nmodel = "old"\n', encoding="utf-8")
    target_path.chmod(0o644)

    written = write_agents_toml(
        target_path, '[agents.claude]\nmodel = "new"\n', trigger="mode_preserved"
    )

    assert written is True
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o644


def test_write_preserves_restrictive_existing_file_mode(tmp_path: Path) -> None:
    """Rewriting an existing file keeps its restrictive 0600 mode."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    target_path.write_text('[agents.claude]\nmodel = "old"\n', encoding="utf-8")
    target_path.chmod(0o600)

    written = write_agents_toml(
        target_path, '[agents.claude]\nmodel = "new"\n', trigger="mode_preserved_restrictive"
    )

    assert written is True
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o600


def test_new_file_is_created_with_mode_0644(tmp_path: Path) -> None:
    """A newly created file gets mode 0644 instead of mkstemp's 0600."""
    target_path = tmp_path / "ralph-workflow-agents.toml"

    written = write_agents_toml(
        target_path, '[agents.claude]\nmodel = "new"\n', trigger="new_file_mode"
    )

    assert written is True
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o644


def test_write_emits_operator_visible_loguru_line(tmp_path: Path) -> None:
    """The trigger line reaches an operator-visible loguru sink in one line."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    content = '[agents.claude]\nmodel = "visible"\n'
    bak_path = target_path.with_name(f"{target_path.name}.bak")

    with _capture_loguru_lines() as lines:
        written = write_agents_toml(target_path, content, trigger="operator_visible")

    assert written is True
    assert len(lines) == 1
    line = str(lines[0])
    assert str(target_path) in line
    assert "operator_visible" in line
    assert str(bak_path) in line


def test_failed_write_leaves_no_backup_and_no_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed replace propagates unchanged and leaves neither a .bak nor a temp file."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    original_content = '[agents.claude]\nmodel = "old"\n'
    target_path.write_text(original_content, encoding="utf-8")
    bak_path = target_path.with_name(f"{target_path.name}.bak")

    def failing_replace(
        src: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        dst: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        raise OSError("Simulated replace failure")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="Simulated replace failure") as excinfo:
        write_agents_toml(target_path, '[agents.claude]\nmodel = "new"\n', trigger="failed_write")

    assert excinfo.value.args == ("Simulated replace failure",)
    assert not bak_path.exists()
    assert target_path.read_text(encoding="utf-8") == original_content
    assert [entry.name for entry in tmp_path.iterdir()] == [target_path.name]


def test_backup_exists_before_the_replace_overwrites_the_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The previous bytes are recoverable on disk before the target is replaced."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    original_content = '[agents.claude]\nmodel = "old"\n'
    target_path.write_text(original_content, encoding="utf-8")
    bak_path = target_path.with_name(f"{target_path.name}.bak")
    observed: list[bytes | None] = []
    real_replace = os.replace

    def observing_replace(
        src: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        dst: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        observed.append(bak_path.read_bytes() if bak_path.is_file() else None)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", observing_replace)

    write_agents_toml(target_path, '[agents.claude]\nmodel = "new"\n', trigger="backup_order")

    assert observed == [original_content.encode("utf-8")]
    assert bak_path.read_text(encoding="utf-8") == original_content
    assert target_path.read_text(encoding="utf-8") == '[agents.claude]\nmodel = "new"\n'


def test_module_imports_only_stdlib_and_loguru() -> None:
    """Module must import only standard library and loguru, never a ralph module."""
    import ralph.config.agents_file_writer as writer_mod  # probe-marker

    assert writer_mod.__file__ is not None
    source_path = Path(writer_mod.__file__)
    parsed = ast.parse(source_path.read_text(encoding="utf-8"))

    allowed_top_levels = {
        "__future__",
        "contextlib",
        "logging",
        "os",
        "pathlib",
        "stat",
        "tempfile",
        "tomllib",
        "loguru",
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
