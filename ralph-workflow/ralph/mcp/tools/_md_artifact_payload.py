"""Payload serialization helpers for ``md_artifact`` tool responses.

Extracted from ``md_artifact.py`` to keep that module under the 1000-line
audit cap. The helpers are pure (no session or workspace state) and are
the only callers' serialization path; moving them keeps the JSON contract
unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.markdown.specs.plan import _OverrideMatch

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown import Diagnostic


def severity_counts(diagnostics: list[Diagnostic]) -> dict[str, int]:
    """Return per-severity counts so callers can see the warning/Info load at a glance."""
    counts = {"error": 0, "warning": 0, "info": 0}
    for diagnostic in diagnostics:
        if diagnostic.severity in counts:
            counts[diagnostic.severity] += 1
    return counts


def diagnostic_payload(diagnostic: Diagnostic) -> dict[str, object]:
    """Serialize one Diagnostic for the tool response with explicit field types.

    Hand-built to avoid ``dataclasses.asdict``'s ``dict[str, Any]`` return
    type colliding with the strict ``disallow_any_expr`` mypy policy; the
    JSON contract is unchanged.
    """
    return {
        "line": diagnostic.line,
        "section": diagnostic.section,
        "rule_id": diagnostic.rule_id,
        "message": diagnostic.message,
        "severity": diagnostic.severity,
    }


def override_payload(match: object) -> dict[str, object]:
    """Serialize an OverrideMatch dataclass for the tool response.

    The tool layer accepts any object the plan validator's analyze entry point
    produces; we narrow to the OverrideMatch shape via dataclass detection and
    fall back to a minimal payload for unexpected objects so the tool never
    raises a serialization error on the hot path.
    """
    if isinstance(match, _OverrideMatch):
        diagnostic_dict: dict[str, object] = (
            diagnostic_payload(match.diagnostic) if match.diagnostic is not None else {}
        )
        return {
            "rule_id": match.rule_id,
            "section": match.section,
            "reason": match.reason,
            "diagnostic": diagnostic_dict,
        }
    return cast("dict[str, object]", match)


__all__ = [
    "diagnostic_payload",
    "override_payload",
    "severity_counts",
]
