"""Helpers for reading plan and analysis-decision artifacts.

These readers are intentionally tolerant: missing files, malformed artifacts,
or unexpected schemas all return ``None`` rather than raising. This keeps the
display resilient when artifacts are partially written or absent (for example
during the first iteration before any analysis has run).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.display.plan_summary import PlanSummary
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

ARTIFACTS_DIR_REL = ".agent/artifacts"
PLAN_ARTIFACT_REL = "plan.md"


@dataclass(frozen=True, slots=True)
class AnalysisDecisionSummary:
    """A stable projection of an ``*_analysis_decision`` artifact."""

    drain: str
    decision: str
    reason: str | None = None
    iso_ts: str | None = None


def _load_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, PermissionError):
        return None


def _coerce_str_tuple(value: object, *, max_items: int = 64) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for entry in value:
        if isinstance(entry, str):
            text = entry.strip()
            if text:
                items.append(text)
        elif isinstance(entry, dict):
            text_obj = entry.get("text")
            if isinstance(text_obj, str) and text_obj.strip():
                items.append(text_obj.strip())
                continue
            risk_obj = entry.get("risk")
            if isinstance(risk_obj, str) and risk_obj.strip():
                items.append(risk_obj.strip())
        if len(items) >= max_items:
            break
    return tuple(items)


def plan_artifact_path(workspace_root: Path) -> Path:
    """Return the canonical Markdown plan artifact path."""
    return workspace_root / ARTIFACTS_DIR_REL / PLAN_ARTIFACT_REL


def read_plan_artifact(
    workspace_root: Path,
    *,
    _text_loader: Callable[[Path], str | None] = _load_text,
) -> PlanSummary | None:
    """Read ``.agent/artifacts/plan.md`` and project a PlanSummary.

    Returns ``None`` if the file is missing or malformed beyond recovery.
    Warnings from lenient vocabulary normalization do not hide an otherwise
    valid plan from the display.
    """
    markdown = _text_loader(plan_artifact_path(workspace_root))
    if markdown is None:
        return None

    content, diagnostics = parse_and_validate(markdown, PLAN_SPEC)
    if any(diagnostic.severity == "error" for diagnostic in diagnostics):
        return None

    steps_obj = content.get("steps")
    total_steps = len(steps_obj) if isinstance(steps_obj, list) else 0

    return PlanSummary(
        summary=_extract_summary_text(markdown),
        scope_items=_extract_section_bullets(markdown, "Scope"),
        total_steps=total_steps,
        risks_mitigations=_extract_section_bullets(markdown, "Risks"),
    )


def _extract_summary_text(markdown: str) -> str | None:
    """Return the first non-heading line under a ``## Summary`` section.

    The free-form plan contract accepts any readable prose; the display
    surface only needs the first descriptive line for status panels.
    """
    in_summary = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            in_summary = stripped == "## Summary"
            continue
        if in_summary and stripped and not stripped.startswith("#"):
            return stripped
    return None


def _extract_section_bullets(markdown: str, heading: str) -> tuple[str, ...]:
    """Return bullet texts under ``## <heading>`` (best-effort).

    The free-form plan contract does not extract structured risks or
    scope items; the display reader scans the raw markdown for bullet
    lines under the named heading so users still see the plan's intent
    in the status panel.
    """
    in_section = False
    bullets: list[str] = []
    target = f"## {heading}"
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            in_section = stripped == target
            continue
        if not in_section:
            continue
        if stripped.startswith("#"):
            in_section = False
            continue
        if not stripped:
            continue
        if stripped.startswith(("-", "*")):
            text = stripped.lstrip("-*").strip()
            if text:
                bullets.append(_normalize_bullet(text))
        elif stripped.startswith("["):
            bullets.append(_normalize_bullet(stripped))
    return tuple(bullets)


def _normalize_bullet(text: str) -> str:
    """Strip ``[R-1]`` / ``[SC-2]`` style identifiers from bullets.

    The display reader surfaces risk and scope intents by their subject;
    the bracket identifier is scaffolding, not part of the human-visible
    intent.
    """
    if text.startswith("["):
        end = text.find("]")
        if end > 0:
            return text[end + 1 :].strip()
    return text


def read_latest_analysis_decision(
    workspace_root: Path,
    drain: str,
) -> AnalysisDecisionSummary | None:
    """Read the canonical Markdown decision artifact for ``drain``."""
    artifact_type = f"{drain}_decision"
    artifact_path = workspace_root / ARTIFACTS_DIR_REL / f"{artifact_type}.md"
    markdown = _load_text(artifact_path)
    if markdown is None:
        return None

    content, diagnostics = parse_and_validate(markdown, get_spec(artifact_type))
    if any(diagnostic.severity == "error" for diagnostic in diagnostics):
        return None

    decision = content.get("decision") or content.get("status")
    if not isinstance(decision, str) or not decision.strip():
        return None
    reason_obj = content.get("reason") or content.get("summary") or content.get("message")
    reason = reason_obj.strip() if isinstance(reason_obj, str) and reason_obj.strip() else None
    ts_obj = content.get("timestamp") or content.get("updated_at")
    ts = ts_obj if isinstance(ts_obj, str) else None
    return AnalysisDecisionSummary(
        drain=drain,
        decision=decision.strip().lower(),
        reason=reason,
        iso_ts=ts,
    )


__all__ = [
    "ARTIFACTS_DIR_REL",
    "PLAN_ARTIFACT_REL",
    "AnalysisDecisionSummary",
    "PlanSummary",
    "plan_artifact_path",
    "read_latest_analysis_decision",
    "read_plan_artifact",
]
