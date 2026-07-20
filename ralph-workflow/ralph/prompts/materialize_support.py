"""Shared helper utilities for prompt materialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.prompts.payload_refs import build_prompt_payload_variables, write_payload_to_directory
from ralph.prompts.types import SessionCapabilities, capability_template_variables

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
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str:
    """Persist the active prompt content to the workspace prompt file."""
    current_prompt_path = (
        worker_namespace / "tmp" / "CURRENT_PROMPT.md"
        if worker_namespace is not None
        else workspace_root / ".agent" / "CURRENT_PROMPT.md"
    )
    backend.mkdir(current_prompt_path.parent, parents=True, exist_ok=True)
    if prompt_content is None and backend.exists(current_prompt_path):
        return str(current_prompt_path)
    write_text_if_changed(
        backend,
        current_prompt_path,
        prompt_content or "No requirements provided",
        encoding="utf-8",
    )
    return str(current_prompt_path)


def current_prompt_variables(
    prompt_content: str | None,
    current_prompt_path: str,
) -> dict[str, str]:
    """Return the prompt variables for the current prompt path."""
    del prompt_content
    return {"PROMPT": "", "PROMPT_PATH": current_prompt_path}


def merged_variables(base: dict[str, str], session_caps: SessionCapabilities) -> dict[str, str]:
    """Merge base template variables with session capability variables."""
    return {
        **base,
        **capability_template_variables(
            session_caps.capabilities,
            session_caps.policy_flags,
            tool_name_prefix=session_caps.tool_name_prefix,
        ),
    }
