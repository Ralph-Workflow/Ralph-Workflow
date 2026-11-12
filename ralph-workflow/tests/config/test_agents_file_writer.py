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

from ralph.config.agents_file_writer import AgentsFileConflictError, write_agents_toml
from ralph.config.bootstrap import ensure_global_config


@contextmanager
def _capture_loguru_lines() -> Iterator[list[str]]:
    lines: list[str] = []
    sink_id = loguru_logger.add(lines.append, level="INFO", format="{message}")
    try:
        yield lines
    finally:
        loguru_logger.remove(sink_id)


def test_invalid_toml_raises_value_error_and_does_not_create_file(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "ralph-workflow-agents.toml"
    invalid_toml = "agents: invalid_syntax = [unclosed"

    with pytest.raises(ValueError, match="Invalid TOML"):
        write_agents_toml(target_path, invalid_toml, trigger="test_invalid")

    assert not target_path.exists()
    assert not target_path.with_name(f"{target_path.name}.bak").exists()


def test_invalid_toml_leaves_existing_file_untouched(tmp_path: Path) -> None:
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
    target_path = tmp_path / "ralph-workflow-agents.toml"
    content = '[agents.claude]\nmodel = "claude-3-5-sonnet"\n'
    bak_path = target_path.with_name(f"{target_path.name}.bak")

    with caplog.at_level(logging.INFO):
        written = write_agents_toml(target_path, content, trigger="initial_creation")

    assert written is True
    assert target_path.exists()
    assert target_path.read_text(encoding="utf-8") == content
    assert not bak_path.exists()

    assert str(target_path) in caplog.text
    assert "initial_creation" in caplog.text
    assert str(bak_path) in caplog.text


def test_write_creates_backup_when_content_differs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
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
    target_path = tmp_path / "ralph-workflow-agents.toml"
    target_path.write_text('[agents.claude]\nmodel = "old"\n', encoding="utf-8")
    target_path.chmod(0o644)

    written = write_agents_toml(
        target_path, '[agents.claude]\nmodel = "new"\n', trigger="mode_preserved"
    )

    assert written is True
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o644


def test_write_preserves_restrictive_existing_file_mode(tmp_path: Path) -> None:
    target_path = tmp_path / "ralph-workflow-agents.toml"
    target_path.write_text('[agents.claude]\nmodel = "old"\n', encoding="utf-8")
    target_path.chmod(0o600)

    written = write_agents_toml(
        target_path, '[agents.claude]\nmodel = "new"\n', trigger="mode_preserved_restrictive"
    )

    assert written is True
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o600


def test_new_file_is_created_with_mode_0644(tmp_path: Path) -> None:
    target_path = tmp_path / "ralph-workflow-agents.toml"

    written = write_agents_toml(
        target_path, '[agents.claude]\nmodel = "new"\n', trigger="new_file_mode"
    )

    assert written is True
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o644


def test_write_emits_operator_visible_loguru_line(tmp_path: Path) -> None:
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


def test_failed_write_retains_pre_write_backup_and_cleans_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_path = tmp_path / "ralph-workflow-agents.toml"
    original_content = '[agents.claude]\nmodel = "old"\n'
    target_path.write_text(original_content, encoding="utf-8")
    bak_path = target_path.with_name(f"{target_path.name}.bak")
    real_replace = os.replace

    def failing_replace(
        src: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        dst: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        if str(dst) == str(target_path):
            raise OSError("Simulated replace failure")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="Simulated replace failure") as excinfo:
        write_agents_toml(target_path, '[agents.claude]\nmodel = "new"\n', trigger="failed_write")

    assert excinfo.value.args == ("Simulated replace failure",)
    assert bak_path.read_text(encoding="utf-8") == original_content
    assert target_path.read_text(encoding="utf-8") == original_content
    assert sorted(entry.name for entry in tmp_path.iterdir()) == sorted(
        [target_path.name, bak_path.name]
    )


def test_backup_exists_before_the_replace_overwrites_the_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_path = tmp_path / "ralph-workflow-agents.toml"
    original_content = '[agents.claude]\nmodel = "old"\n'
    target_path.write_text(original_content, encoding="utf-8")
    bak_path = target_path.with_name(f"{target_path.name}.bak")
    observed: list[tuple[str, bytes | None]] = []
    real_replace = os.replace

    def observing_replace(
        src: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        dst: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        observed.append((str(dst), bak_path.read_bytes() if bak_path.is_file() else None))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", observing_replace)

    write_agents_toml(target_path, '[agents.claude]\nmodel = "new"\n', trigger="backup_order")

    target_moves = [entry for entry in observed if entry[0] == str(target_path)]
    assert len(target_moves) == 1
    assert target_moves[0][1] == original_content.encode("utf-8")
    assert bak_path.read_text(encoding="utf-8") == original_content
    assert target_path.read_text(encoding="utf-8") == '[agents.claude]\nmodel = "new"\n'


def test_module_imports_only_stdlib_and_loguru() -> None:
    import ralph.config.agents_file_writer as writer_mod

    assert writer_mod.__file__ is not None
    source_path = Path(writer_mod.__file__)
    parsed = ast.parse(source_path.read_text(encoding="utf-8"))

    allowed_top_levels = {
        "__future__",
        "collections",
        "contextlib",
        "fcntl",
        "hashlib",
        "logging",
        "os",
        "pathlib",
        "stat",
        "sys",
        "tempfile",
        "threading",
        "tomllib",
        "typing",
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


def test_migrate_legacy_global_config_skips_symlink_to_agents_config(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    agents_file = tmp_path / "ralph-workflow-agents.toml"
    content = '[agent_drains]\nanalysis = "legacy_analysis"\ncommit = "legacy_commit"\n'
    agents_file.write_text(content, encoding="utf-8")
    original_bytes = agents_file.read_bytes()

    symlink_path = tmp_path / "ralph-workflow.toml"
    symlink_path.symlink_to(agents_file)

    with caplog.at_level(logging.WARNING), _capture_loguru_lines() as log_lines:
        result = ensure_global_config(global_dir=tmp_path)

    assert result.action == "skipped"
    assert result.backup is None
    assert agents_file.read_bytes() == original_bytes
    assert symlink_path.read_bytes() == original_bytes
    assert not (tmp_path / "ralph-workflow.toml.bak").exists()
    assert not (tmp_path / "ralph-workflow-agents.toml.bak").exists()
    assert any("ralph-workflow-agents.toml" in line for line in log_lines) or any(
        "ralph-workflow-agents.toml" in rec.message for rec in caplog.records
    )


def test_migrate_legacy_global_config_skips_shared_custom_name_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    shared_file = tmp_path / "shared-custom.toml"
    shared_file.write_text(
        '[agent_drains]\nanalysis = "legacy_analysis"\ncommit = "legacy_commit"\n',
        encoding="utf-8",
    )
    original_bytes = shared_file.read_bytes()

    main_link = tmp_path / "ralph-workflow.toml"
    main_link.symlink_to(shared_file)
    agents_link = tmp_path / "ralph-workflow-agents.toml"
    agents_link.symlink_to(shared_file)

    with caplog.at_level(logging.WARNING), _capture_loguru_lines() as log_lines:
        result = ensure_global_config(global_dir=tmp_path)

    assert result.action == "skipped"
    assert result.backup is None
    assert shared_file.read_bytes() == original_bytes
    assert not (tmp_path / "ralph-workflow.toml.bak").exists()
    assert not (tmp_path / "ralph-workflow-agents.toml.bak").exists()
    assert not (tmp_path / "shared-custom.toml.bak").exists()
    assert list(tmp_path.glob("*.bak")) == []
    warning_text = "\n".join(log_lines) + "\n" + caplog.text
    assert "ralph-workflow-agents.toml" in warning_text
    assert str(main_link) in warning_text


def test_migrate_legacy_global_config_skips_hard_linked_agents_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    shared_file = tmp_path / "shared-custom.toml"
    shared_file.write_text(
        '[agent_drains]\nanalysis = "legacy_analysis"\ncommit = "legacy_commit"\n',
        encoding="utf-8",
    )
    original_bytes = shared_file.read_bytes()

    main_link = tmp_path / "ralph-workflow.toml"
    os.link(shared_file, main_link)
    agents_link = tmp_path / "ralph-workflow-agents.toml"
    os.link(shared_file, agents_link)

    with caplog.at_level(logging.WARNING), _capture_loguru_lines() as log_lines:
        result = ensure_global_config(global_dir=tmp_path)

    assert result.action == "skipped"
    assert result.backup is None
    assert shared_file.read_bytes() == original_bytes
    assert list(tmp_path.glob("*.bak")) == []
    warning_text = "\n".join(log_lines) + "\n" + caplog.text
    assert "ralph-workflow-agents.toml" in warning_text
    assert "resolves to the same file" in warning_text


def test_migrate_legacy_global_config_skips_xdg_agents_config_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    xdg_dir = tmp_path / "xdg-config"
    xdg_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))

    backing_file = xdg_dir / "shared-custom.toml"
    backing_file.write_text(
        '[agent_drains]\nanalysis = "legacy_analysis"\ncommit = "legacy_commit"\n',
        encoding="utf-8",
    )
    original_bytes = backing_file.read_bytes()
    agents_file = xdg_dir / "ralph-workflow-agents.toml"
    agents_file.symlink_to(backing_file)

    main_link = tmp_path / "ralph-workflow.toml"
    main_link.symlink_to(backing_file)

    with caplog.at_level(logging.WARNING), _capture_loguru_lines() as log_lines:
        result = ensure_global_config(global_dir=tmp_path)

    assert result.action == "skipped"
    assert result.backup is None
    assert backing_file.read_bytes() == original_bytes
    assert list(tmp_path.glob("*.bak")) == []
    assert list(xdg_dir.glob("*.bak")) == []
    warning_text = "\n".join(log_lines) + "\n" + caplog.text
    assert "ralph-workflow-agents.toml" in warning_text
    assert "resolves to the same file" in warning_text


def test_migrate_legacy_global_config_migrates_plain_main_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xdg_dir = tmp_path / "xdg-config"
    xdg_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))

    target_path = tmp_path / "ralph-workflow.toml"
    content = '[agent_drains]\nanalysis = "legacy_analysis"\ncommit = "legacy_commit"\n'
    target_path.write_text(content, encoding="utf-8")
    original_bytes = target_path.read_bytes()

    result = ensure_global_config(global_dir=tmp_path)

    assert result.action == "regenerated"
    assert result.backup is not None
    assert result.backup.read_bytes() == original_bytes
    drains = tomllib.loads(target_path.read_text(encoding="utf-8"))["agent_drains"]
    assert drains["planning_analysis"] == "legacy_analysis"
    assert drains["development_analysis"] == "legacy_analysis"
    assert drains["development_commit"] == "legacy_commit"


def test_migrate_legacy_global_config_rejects_invalid_candidate_toml(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    target_path = tmp_path / "ralph-workflow.toml"
    content = '[agent_drains]\nanalysis = \'val"quote\'\ncommit = "some_chain"\n'
    target_path.write_text(content, encoding="utf-8")
    original_bytes = target_path.read_bytes()
    bak_path = target_path.with_name(f"{target_path.name}.bak")

    with caplog.at_level(logging.WARNING), _capture_loguru_lines() as log_lines:
        result = ensure_global_config(global_dir=tmp_path)

    assert result.action == "skipped"
    assert result.backup is None
    assert target_path.read_bytes() == original_bytes
    assert not bak_path.exists()
    assert any(rec.levelno >= logging.WARNING for rec in caplog.records) or any(
        "invalid" in line.lower() for line in log_lines
    )


def test_interleaved_operator_write_after_backup_aborts_without_overwrite_or_stale_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An operator edit interleaved after backup publication and before replacement aborts safely.

    Deterministically asserts:
      1. No generated overwrite: the operator edit is preserved.
      2. No stale backup advertised as current: write_agents_toml raises AgentsFileConflictError.
      3. Cleanup of temp state: scratch files in the target directory are removed.
      4. Clear conflict result/log: logs the trigger, path, and recovery guidance.
    """
    target_path = tmp_path / "ralph-workflow-agents.toml"
    initial_content = '[agents.original]\ncommand = "old"\n'
    target_path.write_text(initial_content, encoding="utf-8")
    operator_content = '[agents.operator]\ncommand = "preserve-me"\n'

    real_replace = Path.replace

    def interleaving_replace(
        self: Path, target: str | os.PathLike[str], *args: object, **kwargs: object
    ) -> Path:
        result = real_replace(self, target, *args, **kwargs)
        if str(target).endswith(".bak"):
            target_path.write_text(operator_content, encoding="utf-8")
        return result

    monkeypatch.setattr(Path, "replace", interleaving_replace)

    with (
        caplog.at_level(logging.WARNING),
        _capture_loguru_lines() as log_lines,
        pytest.raises(AgentsFileConflictError) as exc_info,
    ):
        write_agents_toml(
            target_path,
            '[agents.generated]\ncommand = "new"\n',
            trigger="test_conflict_after_backup",
        )

    # 1. No generated overwrite: target must contain the operator edit
    assert target_path.read_text(encoding="utf-8") == operator_content

    # 2. No stale backup advertised as current: error raised and error message details
    assert "Conflict detected" in str(exc_info.value)
    assert str(target_path) in str(exc_info.value)

    # 3. Cleanup of temp state: no transient scratch files survive
    stray_files = [
        f.name
        for f in tmp_path.iterdir()
        if f != target_path and not f.name.endswith(".bak") and not f.name.endswith(".lock")
    ]
    assert stray_files == []

    # 4. Clear conflict result/log: operator-visible log record emitted
    combined_logs = caplog.text + "\n" + "\n".join(log_lines)
    assert "Conflict detected while writing" in combined_logs
    assert "test_conflict_after_backup" in combined_logs
    assert "Aborting write to preserve operator edits without overwrite" in combined_logs
    assert "ralph --regenerate-config" in combined_logs


def test_interleaved_operator_write_during_staging_aborts_without_backup_or_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An operator edit during staging aborts before backup publication, creating no backup."""
    target_path = tmp_path / "ralph-workflow-agents.toml"
    initial_content = '[agents.original]\ncommand = "old"\n'
    target_path.write_text(initial_content, encoding="utf-8")
    operator_content = '[agents.operator]\ncommand = "staged-preserve-me"\n'
    bak_path = target_path.with_name(f"{target_path.name}.bak")

    real_chmod = Path.chmod

    def interleaving_chmod(self: Path, mode: int) -> None:
        real_chmod(self, mode)
        # Interleave operator write during staging (when chmod is called on tmp_path)
        if self != target_path:
            target_path.write_text(operator_content, encoding="utf-8")

    monkeypatch.setattr(Path, "chmod", interleaving_chmod)

    with (
        caplog.at_level(logging.WARNING),
        _capture_loguru_lines(),
        pytest.raises(AgentsFileConflictError),
    ):
        write_agents_toml(
            target_path,
            '[agents.generated]\ncommand = "new"\n',
            trigger="test_conflict_during_staging",
        )

    # Operator content preserved
    assert target_path.read_text(encoding="utf-8") == operator_content
    # No backup was created
    assert not bak_path.exists()
    # Temp files cleaned up
    stray_files = [
        f.name
        for f in tmp_path.iterdir()
        if f != target_path and not f.name.endswith(".bak") and not f.name.endswith(".lock")
    ]
    assert stray_files == []
