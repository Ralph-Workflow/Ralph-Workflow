"""Bounded structured logging for rejected markdown artifact validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown import Diagnostic


def log_validation_rejection(artifact_type: str, diagnostics: list[Diagnostic]) -> None:
    """Record bounded structured details for blocking validation diagnostics."""
    errors = [item for item in diagnostics if item.severity == "error"]
    if not errors:
        return
    details = "; ".join(
        "rule_id={rule_id} line={line} section={section} message={message}".format(
            rule_id=item.rule_id,
            line=item.line,
            section=(item.section or ""),
            message=item.message[:500],
        )
        for item in errors
    )[:1800]
    logger.error(
        "artifact validation rejected artifact_type={artifact_type} diagnostics={details}",
        artifact_type=artifact_type,
        details=details,
    )
