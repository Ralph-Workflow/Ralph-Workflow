from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import AgentConfig

_ATTEMPT_STARTS: deque[tuple[str, int]] = deque(maxlen=64)


def _transcript_path(workspace_root: Path, config: AgentConfig) -> Path | None:
    unit_id = raw_log_unit_id_for(config)
    if not unit_id:
        return None
    return raw_log_path_for(workspace_root, unit_id, model=config.model)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def mark_raw_transcript_attempt_start(workspace_root: Path | None, config: AgentConfig) -> None:
    if workspace_root is None:
        return
    path = _transcript_path(workspace_root, config)
    if path is None:
        return
    _ATTEMPT_STARTS.append((str(path), _size(path)))


def raw_transcript_attempt_start(path: Path) -> int:
    key = str(path)
    for recorded, start in reversed(_ATTEMPT_STARTS):
        if recorded == key:
            return start if _size(path) >= start else 0
    return 0


__all__ = ["mark_raw_transcript_attempt_start", "raw_transcript_attempt_start"]
