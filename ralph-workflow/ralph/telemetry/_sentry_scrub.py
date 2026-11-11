
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScrubPolicy:

    home_prefix: str = ""
    extra_prefixes: tuple[str, ...] = ()


__all__ = ["ScrubPolicy"]
