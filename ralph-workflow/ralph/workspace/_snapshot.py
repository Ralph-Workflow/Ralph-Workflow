
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceSnapshot:

    stat: dict[str, object]
    content: str | None
