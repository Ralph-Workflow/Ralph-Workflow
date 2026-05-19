"""Exit-success pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitSuccessEffect:
    """Effect to exit with success."""

    pass
