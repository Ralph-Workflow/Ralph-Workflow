"""Polished artifact block renderers for plan/analysis/commit/fix artifacts.

These renderers read artifact files and emit rich, titled blocks that are
clearly delimited in the transcript. All output is markup-free and
highlight-free for copy-paste safety.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.rule import Rule

from ralph.display.artifact_reader import (
    read_latest_analysis_decision,
    read_plan_artifact,
)
from ralph.display.phase_banner import _phase_style
from ralph.mcp.artifacts.commit_message import read_commit_message_artifact
from ralph.mcp.artifacts.handoffs import (
    ensure_markdown_handoff_from_artifact,
    handoff_path_for_artifact,
)

if TYPE_CHECKING:
    from rich.console import Console

    from ralph.display.context import DisplayContext


_ARTIFACTS_DIR = ".agent/artifacts"


def _read_text_defensive(path: Path) -> str | None:
    try:
        content = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, PermissionError):
        return None
    return content


def _read_markdown_handoff(workspace_root: Path, artifact_type: str) -> str | None:
    relative_path = handoff_path_for_artifact(artifact_type)
    if relative_path is None:
        return None
    candidate = workspace_root / relative_path
    markdown = _read_text_defensive(candidate)
    if markdown is None:
        return None
    stripped = markdown.strip()
    return stripped or None


def _regenerated_markdown_handoff(
    workspace_root: Path,
    artifact_type: str,
    artifact_path: Path,
) -> str | None:
    artifact_content = _read_text_defensive(artifact_path)
    if artifact_content is None:
        return None
    try:
        created_path = ensure_markdown_handoff_from_artifact(
            workspace_root,
            artifact_type,
            artifact_content,
        )
    except (json.JSONDecodeError, OSError, PermissionError, TypeError, ValueError):
        return None
    if created_path is None:
        return None
    regenerated = _read_text_defensive(Path(created_path))
    if regenerated is None:
        return None
    stripped = regenerated.strip()
    return stripped or None


def _resolve_authoritative_markdown_handoff(
    workspace_root: Path,
    artifact_type: str,
    artifact_path: Path,
) -> str | None:
    regenerated = _regenerated_markdown_handoff(workspace_root, artifact_type, artifact_path)
    if regenerated is not None:
        return regenerated
    return _read_markdown_handoff(workspace_root, artifact_type)


def _render_titled_lines(
    title: str,
    style_phase: str,
    lines: list[str],
    console: Console,
) -> None:
    console.print()
    console.print(Rule(title, style=_phase_style(style_phase)), markup=False, highlight=False)
    for line in lines:
        console.print(line, markup=False, highlight=False)
    console.print(Rule(style=_phase_style(style_phase)), markup=False, highlight=False)


def _render_text_block(
    title: str,
    body: str,
    style_phase: str,
    console: Console,
    *,
    indent: bool = False,
) -> None:
    lines = [line.rstrip() for line in body.splitlines() if line.strip()]
    if indent:
        lines = [f"  {lines[0]}", *[f"    {line}" for line in lines[1:]]] if lines else []
    _render_titled_lines(title, style_phase, lines, console)


def _read_json_defensive(path: Path) -> dict[str, object] | None:
    """Read JSON file defensively, returning None on any error."""
    raw = _read_text_defensive(path)
    if raw is None:
        return None
    try:
        parsed_obj: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed_obj, dict):
        return None
    return cast("dict[str, object]", parsed_obj)


def render_missing_plan_hint(display_context: DisplayContext) -> None:
    """Emit a plain INFO line when the plan artifact is absent at phase completion.

    Call this from the planning phase completion handler when plan.json is not
    on disk so the log stream always has a [plan] entry rather than silence.
    """
    timestamp = datetime.now(UTC).isoformat()
    display_context.console.print(
        f"{timestamp} INFO META [plan] (no plan artifact on disk)",
        markup=False,
        highlight=False,
        no_wrap=True,
    )


def render_plan_artifact(
    workspace_root: Path,
    display_context: DisplayContext,
) -> None:
    """Render the agent-facing plan handoff, falling back to the JSON summary.

    Prefer the authoritative Markdown handoff regenerated from ``plan.json`` when
    that artifact exists. Fall back to ``.agent/PLAN.md`` only when there is no
    structured artifact available. Missing artifacts emit a hint line.
    """
    markdown = _resolve_authoritative_markdown_handoff(
        workspace_root,
        "plan",
        workspace_root / _ARTIFACTS_DIR / "plan.json",
    )
    if markdown:
        _render_text_block("PLAN", markdown, "execution", display_context.console)
        return

    plan = read_plan_artifact(workspace_root)

    if plan is None:
        render_missing_plan_hint(display_context)
        return

    lines: list[str] = []
    if plan.summary:
        lines.append(f"  Context: {plan.summary}")
    if plan.scope_items:
        lines.append("  Scope:")
        lines.extend(f"    - {item}" for item in plan.scope_items)
    if plan.total_steps > 0:
        lines.append(f"  Steps: {plan.total_steps}")
    if plan.risks_mitigations:
        lines.append("  Risks:")
        lines.extend(f"    - {risk}" for risk in plan.risks_mitigations)

    _render_titled_lines("PLAN", "execution", lines, display_context.console)


def render_analysis_decision(
    workspace_root: Path,
    drain: str,
    display_context: DisplayContext,
) -> None:
    """Render an analysis decision artifact as a titled block."""
    artifact_type = _analysis_handoff_artifact_type(drain)
    if artifact_type is not None:
        markdown = _resolve_authoritative_markdown_handoff(
            workspace_root,
            artifact_type,
            workspace_root / _ARTIFACTS_DIR / f"{artifact_type}.json",
        )
        if markdown:
            _render_text_block(
                f"ANALYSIS: {drain}",
                markdown,
                "analysis",
                display_context.console,
            )
            return

    summary = read_latest_analysis_decision(workspace_root, drain)
    if summary is None:
        return

    lines = [f"  decision: {summary.decision}"]
    if summary.reason:
        lines.append(f"  reason: {summary.reason}")
    _render_titled_lines(
        f"ANALYSIS: {drain}",
        "analysis",
        lines,
        display_context.console,
    )


def render_commit_message(
    workspace_root: Path,
    display_context: DisplayContext,
) -> None:
    """Render the commit message artifact as a titled block."""
    try:
        message = read_commit_message_artifact(workspace_root)
    except Exception:
        message = None

    if message is None:
        return

    _render_text_block(
        "COMMIT MESSAGE",
        message,
        "commit",
        display_context.console,
        indent=True,
    )


def _analysis_handoff_artifact_type(drain: str) -> str | None:
    # Derive artifact type using the {drain}_decision naming convention.
    # Canonical drains (development_analysis, review_analysis) have registered
    # handoff paths; custom drain names fall through to read_latest_analysis_decision
    # when no handoff file is found.
    return f"{drain}_decision"


def render_development_artifact(
    workspace_root: Path,
    display_context: DisplayContext,
) -> None:
    """Render development results using the authoritative Markdown handoff."""
    markdown = _resolve_authoritative_markdown_handoff(
        workspace_root,
        "development_result",
        workspace_root / _ARTIFACTS_DIR / "development_result.json",
    )
    if markdown:
        _render_text_block("DEVELOPMENT RESULT", markdown, "execution", display_context.console)
        return

    found = _read_json_defensive(workspace_root / _ARTIFACTS_DIR / "development_result.json")
    if found is None:
        return
    _render_text_block(
        "DEVELOPMENT RESULT",
        json.dumps(found, indent=2),
        "execution",
        display_context.console,
    )


def render_review_artifact(
    workspace_root: Path,
    display_context: DisplayContext,
) -> None:
    """Render review findings using the authoritative Markdown handoff."""
    markdown = _resolve_authoritative_markdown_handoff(
        workspace_root,
        "issues",
        workspace_root / _ARTIFACTS_DIR / "issues.json",
    )
    if markdown:
        _render_text_block("REVIEW ISSUES", markdown, "review", display_context.console)
        return

    found = _read_json_defensive(workspace_root / _ARTIFACTS_DIR / "issues.json")
    if found is None:
        return
    _render_text_block(
        "REVIEW ISSUES",
        json.dumps(found, indent=2),
        "review",
        display_context.console,
    )


def render_fix_artifact(
    workspace_root: Path,
    display_context: DisplayContext,
) -> None:
    """Render fix result artifacts as a titled block."""
    markdown = _resolve_authoritative_markdown_handoff(
        workspace_root,
        "fix_result",
        workspace_root / _ARTIFACTS_DIR / "fix_result.json",
    )
    if markdown:
        _render_text_block("FIX", markdown, "fix", display_context.console)
        return

    found = _first_json_candidate(
        workspace_root / _ARTIFACTS_DIR / "fix_result.json",
        workspace_root / _ARTIFACTS_DIR / "issues.json",
    )
    if found is None:
        return

    lines = _render_fix_json_summary(found)
    _render_titled_lines("FIX", "fix", lines, display_context.console)


def _first_json_candidate(*candidates: Path) -> dict[str, object] | None:
    for candidate in candidates:
        found = _read_json_defensive(candidate)
        if found is not None:
            return found
    return None


def _render_fix_json_summary(found: dict[str, object]) -> list[str]:
    if "issues" in found and isinstance(found["issues"], list):
        return _render_issues_summary(found["issues"])
    if "fixed" in found:
        return _render_fixed_summary(found["fixed"])
    return [f"  Fix artifact: {list(found.keys())[:5]}"]


def _render_issues_summary(issues: list[object]) -> list[str]:
    lines = [f"  {len(issues)} issue(s) addressed:"]
    for issue in issues[:10]:
        if isinstance(issue, dict):
            desc_obj = issue.get("description") or issue.get("message") or str(issue)
        else:
            desc_obj = str(issue)
        lines.append(f"    - {str(desc_obj)[:120]}")
    return lines


def _render_fixed_summary(fixed: object) -> list[str]:
    if isinstance(fixed, list):
        lines = [f"  {len(fixed)} item(s) fixed:"]
        lines.extend(f"    - {str(item)[:120]}" for item in fixed[:10])
        return lines
    return [f"  Fixed: {fixed}"]


__all__ = [
    "render_analysis_decision",
    "render_commit_message",
    "render_development_artifact",
    "render_fix_artifact",
    "render_missing_plan_hint",
    "render_plan_artifact",
    "render_review_artifact",
]
