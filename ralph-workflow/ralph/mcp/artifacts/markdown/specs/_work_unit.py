from __future__ import annotations

from typing import TypedDict


class WorkUnit(TypedDict):
    unit_id: str
    description: str
    allowed_directories: list[str]
    allowed_paths: list[str]
    dependencies: list[str]
    step_ids: list[str]


__all__: list[str] = []
