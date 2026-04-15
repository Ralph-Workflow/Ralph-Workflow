"""System prompt materialization for supported agent transports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.prompts.template_registry import packaged_template_root

if TYPE_CHECKING:
    from pathlib import Path


def materialize_system_prompt(*, workspace_root: Path, name: str) -> str:
    current_prompt_path = workspace_root / ".agent" / "CURRENT_PROMPT.md"
    system_prompt_path = workspace_root / ".agent" / "tmp" / f"{name}_system_prompt.md"
    system_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    system_prompt_path.write_text(
        build_system_prompt(current_prompt_path=str(current_prompt_path)),
        encoding="utf-8",
    )
    return str(system_prompt_path)


def build_system_prompt(*, current_prompt_path: str) -> str:
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
