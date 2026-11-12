from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


@dataclass(frozen=True)
class ProcessRunOptions:
    cwd: str | Path | None = None
    env: Mapping[str, str] | None = None
    timeout: float | None = None
    capture_output: bool = True
    label: str | None = None
    env_remove: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.env is not None and (conflict := self.env_remove.intersection(self.env)):
            raise ValueError(
                f"environment variables both set and removed: {', '.join(sorted(conflict))}"
            )


__all__ = ["ProcessRunOptions"]
