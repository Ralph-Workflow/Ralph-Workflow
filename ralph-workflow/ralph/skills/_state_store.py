from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.skills._capability_state import CapabilityState

if TYPE_CHECKING:
    from ralph.mcp.artifacts.file_backend import FileBackend

_CAPABILITIES_FILE_NAME = "ralph-workflow-capabilities.json"
DEFAULT_STATE_PATH: Path = Path.home() / ".config" / _CAPABILITIES_FILE_NAME


def default_state_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / _CAPABILITIES_FILE_NAME
    return Path.home() / ".config" / _CAPABILITIES_FILE_NAME


def load_capability_state(path: Path | None = None) -> CapabilityState:
    resolved = path if path is not None else default_state_path()
    if not resolved.exists():
        return CapabilityState()
    try:
        text = resolved.read_text(encoding="utf-8")
        state = CapabilityState.model_validate_json(text)
    except (OSError, ValueError, ValidationError):
        return CapabilityState()
    else:
        return state


def save_capability_state(
    state: CapabilityState,
    path: Path | None = None,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    resolved = path if path is not None else default_state_path()
    backend.mkdir(resolved.parent, parents=True, exist_ok=True)
    write_text_if_changed(
        backend,
        resolved,
        state.model_dump_json(indent=2),
        encoding="utf-8",
    )


__all__ = [
    "DEFAULT_STATE_PATH",
    "default_state_path",
    "load_capability_state",
    "save_capability_state",
]
