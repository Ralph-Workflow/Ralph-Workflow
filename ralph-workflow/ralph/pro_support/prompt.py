from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pro_support.env import PROMPT_PATH, _get_env

if TYPE_CHECKING:
    from collections.abc import Mapping


DEFAULT_SOURCE_PROMPT_NAME = "PROMPT.md"


def resolve_effective_prompt_path(
    workspace_root: Path | str,
    env: Mapping[str, str] | None = None,
) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    env_map = _get_env(env)
    raw = env_map.get(PROMPT_PATH, "")
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (root / candidate).resolve()
    return (root / DEFAULT_SOURCE_PROMPT_NAME).resolve()


__all__ = ["DEFAULT_SOURCE_PROMPT_NAME", "resolve_effective_prompt_path"]
