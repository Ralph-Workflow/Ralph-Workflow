"""Shared helper utilities for prompt materialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.prompts.payload_refs import build_prompt_payload_variables, write_payload_to_directory

if TYPE_CHECKING:
    from pathlib import Path


def phase_payload_variables(
    *,
    phase: str,
    workspace_root: Path,
    values: dict[str, str],
    worker_namespace: Path | None = None,
) -> dict[str, str]:
    """Build prompt payload variables, writing oversized values to disk."""
    output_dir = (
        worker_namespace / "tmp" / "prompt_payloads"
        if worker_namespace is not None
        else workspace_root / ".agent" / "tmp" / "prompt_payloads"
    )
    return build_prompt_payload_variables(
        values,
        prompt_name_prefix=phase,
        write_payload=lambda relative_path, content: write_payload_to_directory(
            output_dir,
            relative_path,
            content,
        ),
    )


def persist_current_prompt(
    workspace_root: Path,
    prompt_content: str | None,
    *,
    worker_namespace: Path | None = None,
) -> str:
    """Persist the active prompt content to the workspace prompt file."""
    current_prompt_path = (
        worker_namespace / "tmp" / "CURRENT_PROMPT.md"
        if worker_namespace is not None
        else workspace_root / ".agent" / "CURRENT_PROMPT.md"
    )
    current_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    if prompt_content is None and current_prompt_path.exists():
        return str(current_prompt_path)
    current_prompt_path.write_text(prompt_content or "No requirements provided", encoding="utf-8")
    return str(current_prompt_path)


def current_prompt_variables(
    prompt_content: str | None,
    current_prompt_path: str,
) -> dict[str, str]:
    """Return the prompt variables for the current prompt path."""
    del prompt_content
    return {"PROMPT": "", "PROMPT_PATH": current_prompt_path}
