"""Line-anchored diagnostics for the closed markdown artifact grammar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Diagnostic:
    """One validation finding, anchored to the source document when possible."""

    line: int
    section: str | None
    rule_id: str
    message: str
    severity: Literal["error", "warning"] = "error"


__all__ = ["Diagnostic"]
