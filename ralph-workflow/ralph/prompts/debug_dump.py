"""Helpers for persisting rendered prompts for debugging."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace


def prompt_dump_path(phase: str) -> str:
    normalized = phase.replace("/", "_").replace(" ", "_")
    return f".agent/tmp/{normalized}_prompt.md"


def multimodal_sidecar_path(phase: str) -> str:
    normalized = phase.replace("/", "_").replace(" ", "_")
    return f".agent/tmp/{normalized}_multimodal_handoff.json"


def media_session_path(phase: str) -> str:
    """Path for the persistent media session index written by the MCP server.

    This file accumulates artifact metadata for each media file loaded during
    a session via read_media. The runner reads it at the next prompt
    materialization to carry media context forward across sessions.
    """
    normalized = phase.replace("/", "_").replace(" ", "_")
    return f".agent/tmp/{normalized}_media_session.json"


def dump_rendered_prompt(workspace: Workspace, phase: str, prompt: str) -> str:
    path = prompt_dump_path(phase)
    workspace.write(path, prompt)
    return path
