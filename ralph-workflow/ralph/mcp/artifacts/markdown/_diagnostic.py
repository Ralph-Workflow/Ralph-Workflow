"""Line-anchored diagnostics for the closed markdown artifact grammar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Three-level severity model.
#
#   error   — A named downstream consumer cannot proceed without this change.
#             Blocking: keeps `valid` / `is_error` keys at False, prevents
#             submission of the artifact. Every error message names the
#             downstream consumer so the agent can decide whether the block
#             is the cheapest link for that check.
#   warning — A specific, defensible prediction that this plan will cost the
#             run something concrete (a verification that will bounce, an
#             ambiguity that will produce the wrong change, a check nobody
#             can run). Advisory; overridable via `## Validation Overrides`.
#             If most plans draw most warnings, warnings must be demoted.
#   info    — An observation worth a second look. Never implies the plan is
#             wrong. Stale overrides surface here so suppressions that no
#             longer suppress anything are flagged without blocking submission.
DiagnosticSeverity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Diagnostic:
    """One validation finding, anchored to the source document when possible."""

    line: int
    section: str | None
    rule_id: str
    message: str
    severity: DiagnosticSeverity = "error"


__all__ = ["Diagnostic", "DiagnosticSeverity"]
