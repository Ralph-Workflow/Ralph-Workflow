"""System prompt materialization for supported agent transports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.prompts.template_registry import packaged_template_root

if TYPE_CHECKING:
    from pathlib import Path


def materialize_system_prompt(
    *,
    workspace_root: Path,
    name: str,
    default_current_prompt: str | None = None,
) -> str:
    """Write a system prompt file for the named agent and return its path."""
    current_prompt_path = _sync_current_prompt_file(
        workspace_root=workspace_root,
        default_current_prompt=default_current_prompt,
    )
    system_prompt_path = workspace_root / ".agent" / "tmp" / f"{name}_system_prompt.md"
    system_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    system_prompt_path.write_text(
        build_system_prompt(current_prompt_path=str(current_prompt_path)),
        encoding="utf-8",
    )
    return str(system_prompt_path)


def _sync_current_prompt_file(
    *,
    workspace_root: Path,
    default_current_prompt: str | None,
) -> Path:
    current_prompt_path = workspace_root / ".agent" / "CURRENT_PROMPT.md"
    source_prompt_path = workspace_root / "PROMPT.md"
    current_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    if source_prompt_path.exists():
        prompt_text = source_prompt_path.read_text(encoding="utf-8")
        if (
            not current_prompt_path.exists()
            or current_prompt_path.read_text(encoding="utf-8") != prompt_text
        ):
            current_prompt_path.write_text(prompt_text, encoding="utf-8")
            _write_prompt_history_snapshot(workspace_root=workspace_root, prompt_text=prompt_text)
        return current_prompt_path
    if not current_prompt_path.exists() and default_current_prompt is not None:
        current_prompt_path.write_text(default_current_prompt, encoding="utf-8")
        _write_prompt_history_snapshot(
            workspace_root=workspace_root,
            prompt_text=default_current_prompt,
        )
    return current_prompt_path


def _write_prompt_history_snapshot(*, workspace_root: Path, prompt_text: str) -> None:
    history_dir = workspace_root / ".agent" / "prompt_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / f"PROMPT_{_history_timestamp()}.md"
    history_path.write_text(prompt_text, encoding="utf-8")


def _history_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_system_prompt(*, current_prompt_path: str) -> str:
    """Build the system prompt text that points the agent at the current prompt file."""
    unattended = _unattended_mode_text().strip()
    return (
        f"{unattended}\n\n"
        "Use the canonical user request from this file:\n"
        f"`{current_prompt_path}`\n\n"
        "Treat that file as the source of truth for the current task.\n"
        "Do not ask the user to restate it.\n"
    )


def _unattended_mode_text() -> str:
    return (packaged_template_root() / "shared" / "_unattended_mode.jinja").read_text(
        encoding="utf-8"
    )
