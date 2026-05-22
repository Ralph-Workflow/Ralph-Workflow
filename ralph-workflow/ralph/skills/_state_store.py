"""JSON persistence for capability state."""

from __future__ import annotations

from pathlib import Path

from ralph.skills._state import CapabilityState

DEFAULT_STATE_PATH: Path = Path.home() / ".config" / "ralph-workflow-capabilities.json"


def default_state_path() -> Path:
    return DEFAULT_STATE_PATH


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


def save_capability_state(state: CapabilityState, path: Path | None = None) -> None:
    """Persist capability state to JSON file."""
    resolved = path if path is not None else default_state_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(state.model_dump_json(indent=2), encoding="utf-8")


__all__ = [
    "DEFAULT_STATE_PATH",
    "default_state_path",
    "load_capability_state",
    "save_capability_state",
]
