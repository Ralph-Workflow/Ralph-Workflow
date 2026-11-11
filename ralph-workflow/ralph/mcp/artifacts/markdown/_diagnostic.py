
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DiagnosticSeverity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Diagnostic:

    line: int
    section: str | None
    rule_id: str
    message: str
    severity: DiagnosticSeverity = "error"


__all__ = ["Diagnostic", "DiagnosticSeverity"]
