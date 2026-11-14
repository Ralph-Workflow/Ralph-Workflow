from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["ConfigFailureOutcome"]


@dataclass(frozen=True, slots=True)
class ConfigFailureOutcome:
    file: Path
    parse_error: BaseException | str
    succeeded: bool = False
    reason: object = None
    unresolved_paths: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return False
