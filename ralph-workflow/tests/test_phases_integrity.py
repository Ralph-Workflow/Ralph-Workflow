"""Unit tests for phase integrity helpers."""

from __future__ import annotations

from importlib import import_module

from ralph.workspace.memory import MemoryWorkspace

integrity = import_module("ralph.phases.integrity")


def test_verify_prompt_integrity_accepts_existing_prompt() -> None:
    """A non-empty PROMPT.md should pass integrity checks unchanged."""
    workspace = MemoryWorkspace()
    workspace.write("PROMPT.md", "# Prompt\n")

    result = integrity.verify_prompt_integrity(workspace, prompt_path="PROMPT.md")

    assert result.ok is True
    assert result.restored is False
    assert result.prompt_path == "PROMPT.md"


def test_ensure_prompt_integrity_restores_missing_prompt_from_backup() -> None:
    """Missing PROMPT.md should be restored from a backup copy when available."""
    workspace = MemoryWorkspace()
    workspace.write(".agent/prompt.backup.md", "# Restored\n")

    result = integrity.ensure_prompt_integrity(
        workspace,
        phase="development",
        iteration=2,
        prompt_path="PROMPT.md",
    )

    assert result.ok is True
    assert result.restored is True
    assert workspace.read("PROMPT.md") == "# Restored\n"
    assert "development" in result.message


def test_ensure_prompt_integrity_restores_empty_prompt_from_backup() -> None:
    """Empty PROMPT.md should be treated as broken and restored."""
    workspace = MemoryWorkspace()
    workspace.write("PROMPT.md", "   \n")
    workspace.write(".agent/prompt.backup.md", "# Backup\n")

    result = integrity.ensure_prompt_integrity(
        workspace, phase="review", iteration=1, prompt_path="PROMPT.md"
    )

    assert result.ok is True
    assert result.restored is True
    assert workspace.read("PROMPT.md") == "# Backup\n"


def test_ensure_prompt_integrity_reports_failure_without_backup() -> None:
    """Missing prompt without a backup should return a failure result."""
    workspace = MemoryWorkspace()

    result = integrity.ensure_prompt_integrity(
        workspace, phase="development", iteration=3, prompt_path="PROMPT.md"
    )

    assert result.ok is False
    assert result.restored is False
    assert "development" in result.message
    assert "PROMPT.md" in result.message
