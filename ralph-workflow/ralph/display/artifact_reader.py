"""Helpers for reading plan and analysis-decision artifacts.

These readers are intentionally tolerant: missing files, malformed JSON, or
unexpected schemas all return ``None`` rather than raising. This keeps the
display resilient when artifacts are partially written or absent (for
example during the first iteration before any analysis has run).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pathlib import Path

ARTIFACTS_DIR_REL = ".agent/artifacts"
PLAN_ARTIFACT_REL = "plan.json"


@dataclass(frozen=True, slots=True)
class AnalysisDecisionSummary:
    """A stable projection of an ``*_analysis_decision.json`` artifact."""

    @dataclass(frozen=True, slots=True)
    class PlanSummary:
        """A stable, presentation-friendly projection of a plan.json artifact."""

        summary: str | None = None
        scope_items: tuple[str, ...] = ()
        total_steps: int = 0
        risks_mitigations: tuple[str, ...] = field(default_factory=tuple)


    drain: str
    decision: str
    reason: str | None = None
    iso_ts: str | None = None


PlanSummary = AnalysisDecisionSummary.PlanSummary


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, PermissionError):
        return None
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return cast("dict[str, object]", parsed)


def _content_dict(artifact: dict[str, object]) -> dict[str, object]:
    content = artifact.get("content")
    if isinstance(content, dict):
        return cast("dict[str, object]", content)
    return artifact


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


def read_plan_artifact(workspace_root: Path) -> PlanSummary | None:
    """Read ``.agent/artifacts/plan.json`` and project a PlanSummary.

    Returns ``None`` if the file is missing or malformed beyond recovery.
    Always returns a populated PlanSummary when the file parses, even if some
    fields are missing — empty defaults (``""``, ``()``, ``0``) are filled in.
    """
    artifact_path = workspace_root / ARTIFACTS_DIR_REL / PLAN_ARTIFACT_REL
    artifact = _load_json(artifact_path)
    if artifact is None:
        return None

    content = _content_dict(artifact)

    summary_obj = content.get("summary")
    summary_text: str | None = None
    scope_items: tuple[str, ...] = ()
    if isinstance(summary_obj, dict):
        summary_dict = cast("dict[str, object]", summary_obj)
        ctx = summary_dict.get("context")
        if isinstance(ctx, str) and ctx.strip():
            summary_text = ctx.strip()
        scope_items = _coerce_str_tuple(summary_dict.get("scope_items"))

    steps_obj = content.get("steps")
    total_steps = len(steps_obj) if isinstance(steps_obj, list) else 0

    risks = _coerce_str_tuple(content.get("risks_mitigations"))

    return PlanSummary(
        summary=summary_text,
        scope_items=scope_items,
        total_steps=total_steps,
        risks_mitigations=risks,
    )


def read_latest_analysis_decision(
    workspace_root: Path,
    drain: str,
) -> AnalysisDecisionSummary | None:
    """Read the latest decision artifact for ``drain``.

    Looks at ``{drain}_decision.json`` first (canonical name used by phase
    handlers), then ``{drain}.json``.
    """
    artifacts_dir = workspace_root / ARTIFACTS_DIR_REL
    candidate_names = (f"{drain}_decision.json", f"{drain}.json")
    for name in candidate_names:
        artifact = _load_json(artifacts_dir / name)
        if artifact is None:
            continue
        content = _content_dict(artifact)

        decision = content.get("decision") or content.get("status")
        if not isinstance(decision, str) or not decision.strip():
            continue
        reason_obj = content.get("reason") or content.get("summary") or content.get("message")
        reason: str | None = None
        if isinstance(reason_obj, str) and reason_obj.strip():
            reason = reason_obj.strip()
        ts_obj = content.get("timestamp") or content.get("updated_at") or artifact.get("updated_at")
        ts: str | None = ts_obj if isinstance(ts_obj, str) else None
        return AnalysisDecisionSummary(
            drain=drain,
            decision=decision.strip().lower(),
            reason=reason,
            iso_ts=ts,
        )
    return None


__all__ = [
    "ARTIFACTS_DIR_REL",
    "PLAN_ARTIFACT_REL",
    "AnalysisDecisionSummary",
    "PlanSummary",
    "read_latest_analysis_decision",
    "read_plan_artifact",
]
