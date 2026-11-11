
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["BootstrapResult"]


@dataclass(frozen=True)
class BootstrapResult:

    path: Path
    action: Literal["created", "skipped", "regenerated"]
    backup: Path | None = None
