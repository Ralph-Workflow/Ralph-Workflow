
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

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
    return workspace_root / ARTIFACTS_DIR_REL / PLAN_ARTIFACT_REL


def read_plan_artifact(
    workspace_root: Path,
    *,
    _text_loader: Callable[[Path], str | None] = _load_text,
) -> PlanSummary | None:
    markdown = _text_loader(plan_artifact_path(workspace_root))
    if markdown is None:
        return None

    content, diagnostics = parse_and_validate(markdown, PLAN_SPEC)
    if any(diagnostic.severity == "error" for diagnostic in diagnostics):
        return None

    summary_obj = content.get("summary")
    summary_text: str | None = None
    scope_items: tuple[str, ...] = ()
    if isinstance(summary_obj, dict):
        summary_dict = cast("dict[str, object]", summary_obj)
        ctx = summary_dict.get("context")
        if isinstance(ctx, str) and ctx.strip():
            summary_text = ctx.strip()
        else:
            intent = summary_dict.get("intent_verb")
            if isinstance(intent, str) and intent.strip():
                summary_text = intent.strip()
        scope_items = _coerce_str_tuple(summary_dict.get("scope_items"))

    if summary_text is None:
        seen_open = False
        seen_close = False
        for line in markdown.splitlines():
            stripped = line.strip()
            if stripped == "---":
                if not seen_open:
                    seen_open = True
                    continue
                seen_close = True
                continue
            if seen_close and stripped and not stripped.startswith("#"):
                summary_text = stripped
                break

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
