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
    worker_namespace: Path | None = None,
) -> str:
    """Write a system prompt file for the named agent and return its path."""
    current_prompt_path = _sync_current_prompt_file(
        workspace_root=workspace_root,
        default_current_prompt=default_current_prompt,
        worker_namespace=worker_namespace,
    )
    current_plan_path = _current_plan_handoff_path(workspace_root, phase_name=name)
    system_prompt_path = (
        worker_system_prompt_path(worker_namespace, name)
        if worker_namespace is not None
        else workspace_root / ".agent" / "tmp" / f"{name}_system_prompt.md"
    )
    system_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    system_prompt_path.write_text(
        build_system_prompt(
            phase_name=name,
            current_prompt_path=str(current_prompt_path),
            current_plan_path=str(current_plan_path) if current_plan_path is not None else None,
        ),
        encoding="utf-8",
    )
    return str(system_prompt_path)


def _sync_current_prompt_file(
    *,
    workspace_root: Path,
    default_current_prompt: str | None,
    worker_namespace: Path | None = None,
) -> Path:
    current_prompt_path = (
        worker_current_prompt_path(worker_namespace)
        if worker_namespace is not None
        else workspace_root / ".agent" / "CURRENT_PROMPT.md"
    )
    source_prompt_path = workspace_root / "PROMPT.md"
    current_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    if source_prompt_path.exists():
        prompt_text = source_prompt_path.read_text(encoding="utf-8")
        if (
            not current_prompt_path.exists()
            or current_prompt_path.read_text(encoding="utf-8") != prompt_text
        ):
            current_prompt_path.write_text(prompt_text, encoding="utf-8")
            if worker_namespace is None:
                _write_prompt_history_snapshot(
                    workspace_root=workspace_root,
                    prompt_text=prompt_text,
                )
        return current_prompt_path
    if not current_prompt_path.exists() and default_current_prompt is not None:
        current_prompt_path.write_text(default_current_prompt, encoding="utf-8")
        if worker_namespace is None:
            _write_prompt_history_snapshot(
                workspace_root=workspace_root,
                prompt_text=default_current_prompt,
            )
    return current_prompt_path


def worker_current_prompt_path(worker_namespace: Path) -> Path:
    """Return the worker-local mirror path for CURRENT_PROMPT.md."""
    return worker_namespace / "tmp" / "CURRENT_PROMPT.md"


def worker_system_prompt_path(worker_namespace: Path, phase: str) -> Path:
    """Return the worker-local system prompt materialization path."""
    normalized = phase.replace("/", "_").replace(" ", "_")
    return worker_namespace / "tmp" / f"{normalized}_system_prompt.md"


def _write_prompt_history_snapshot(*, workspace_root: Path, prompt_text: str) -> None:
    history_dir = workspace_root / ".agent" / "prompt_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / f"PROMPT_{_history_timestamp()}.md"
    history_path.write_text(prompt_text, encoding="utf-8")


def _history_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _current_plan_handoff_path(workspace_root: Path, *, phase_name: str) -> Path | None:
    if phase_name == "planning":
        return None
    plan_path = workspace_root / ".agent" / "PLAN.md"
    return plan_path if plan_path.exists() else None


def build_system_prompt(
    *,
    phase_name: str,
    current_prompt_path: str,
    current_plan_path: str | None = None,
) -> str:
    """Build the system prompt text that points the agent at durable task context files."""
    unattended = _unattended_mode_text().strip()
    if phase_name == "planning":
        return (
            f"{unattended}\n\n"
            "Use the canonical task request from this file:\n"
            f"`{current_prompt_path}`\n\n"
            "Treat that file as the source of truth for the current goal.\n"
            "Do not ask the user to restate it.\n"
        )

    plan_guidance = ""
    if current_plan_path is not None:
        plan_guidance = (
            "\n"
            "Use the canonical plan from this file whenever it exists:\n"
            f"`{current_plan_path}`\n\n"
            "Treat that file as the source of truth for the current goal and execution steps, "
            "especially after any context compaction, resume, or continuation.\n"
        )
    return (
        f"{unattended}\n\n"
        "Use the canonical task context from this file:\n"
        f"`{current_prompt_path}`\n\n"
        "Treat that file as background context for the current task.\n"
        "Do not ask the user to restate it.\n"
        f"{plan_guidance}"
    )


def _unattended_mode_text() -> str:
    return (packaged_template_root() / "shared" / "_unattended_mode.jinja").read_text(
        encoding="utf-8"
    )
