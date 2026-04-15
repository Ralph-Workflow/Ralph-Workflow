"""Helpers for persisting rendered prompts for debugging."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace


def prompt_dump_path(phase: str) -> str:
    normalized = phase.replace("/", "_").replace(" ", "_")
    return f".agent/tmp/{normalized}_prompt.md"


def dump_rendered_prompt(workspace: Workspace, phase: str, prompt: str) -> str:
    path = prompt_dump_path(phase)
    workspace.write(path, prompt)
    return path
