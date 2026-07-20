"""JSON persistence for capability state."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.skills._capability_state import CapabilityState

if TYPE_CHECKING:
    from ralph.mcp.artifacts.file_backend import FileBackend

DEFAULT_STATE_PATH: Path = Path.home() / ".config" / "ralph-workflow-capabilities.json"


def default_state_path() -> Path:
    return Path.home() / ".config" / "ralph-workflow-capabilities.json"


def load_capability_state(path: Path | None = None) -> CapabilityState:
    """Load capability state from JSON file, returning empty state on errors."""
    resolved = path if path is not None else default_state_path()
    if not resolved.exists():
        return CapabilityState()
    try:
        text = resolved.read_text(encoding="utf-8")
        return CapabilityState.model_validate_json(text)
    except Exception:
        return CapabilityState()


def save_capability_state(
    state: CapabilityState,
    path: Path | None = None,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Persist capability state to JSON file."""
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
