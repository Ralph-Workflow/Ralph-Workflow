from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UpstreamTool:
    name: str
    description: str
    input_schema: dict[str, object] = field(default_factory=dict)


class UpstreamCallError(Exception):
    pass


__all__ = [
    "UpstreamCallError",
    "UpstreamTool",
]
