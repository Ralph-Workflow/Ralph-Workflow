"""Polished artifact block renderers for plan/analysis/commit/fix artifacts.

These renderers read artifact files and emit rich, titled blocks that are
clearly delimited in the transcript. All output is markup-free and
highlight-free for copy-paste safety.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from rich.rule import Rule

from ralph.display.artifact_reader import (
    read_latest_analysis_decision,
    read_plan_artifact,
)
from ralph.display.phase_banner import _phase_style
from ralph.mcp.artifacts.commit_message import read_commit_message_artifact
from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console


_ARTIFACTS_DIR = ".agent/artifacts"


def _read_markdown_handoff(workspace_root: Path, artifact_type: str) -> str | None:
    relative_path = handoff_path_for_artifact(artifact_type)
    if relative_path is None:
        return None
    candidate = workspace_root / relative_path
    try:
        markdown = candidate.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError, PermissionError):
        return None
    return markdown or None


def _render_markdown_block(title: str, body: str, style_phase: str, console: Console) -> None:
    console.print()
    console.print(Rule(title, style=_phase_style(style_phase)), markup=False, highlight=False)
    console.print(body, markup=False, highlight=False)
    console.print(Rule(style=_phase_style(style_phase)), markup=False, highlight=False)


def _read_json_defensive(path: Path) -> dict[str, object] | None:
    """Read JSON file defensively, returning None on any error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, PermissionError):
        return None
    try:
        parsed_obj: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed_obj, dict):
        return None
    return cast("dict[str, object]", parsed_obj)


def render_plan_artifact(
    workspace_root: Path,
    console: Console,
) -> None:
    """Render the agent-facing plan handoff, falling back to the JSON summary.

    Prefer ``.agent/PLAN.md`` because that is the human/agent communication
    artifact. Fall back to ``.agent/artifacts/plan.json`` only when the Markdown
    handoff is unavailable. Missing artifacts produce no output.
    """
    markdown = _read_markdown_handoff(workspace_root, "plan")
    if markdown:
        _render_markdown_block("PLAN", markdown, "planning", console)
        return

    plan = read_plan_artifact(workspace_root)

    if plan is None:
        # Missing or malformed - no output per spec
        return

    console.print()
    console.print(
        Rule("PLAN", style=_phase_style("planning")),
        markup=False,
        highlight=False,
    )

    if plan.summary:
        console.print(f"  Context: {plan.summary}", markup=False, highlight=False)

    if plan.scope_items:
        console.print("  Scope:", markup=False, highlight=False)
        for item in plan.scope_items:
            console.print(f"    - {item}", markup=False, highlight=False)

    if plan.total_steps > 0:
        console.print(f"  Steps: {plan.total_steps}", markup=False, highlight=False)

    if plan.risks_mitigations:
        console.print("  Risks:", markup=False, highlight=False)
        for risk in plan.risks_mitigations:
            console.print(f"    - {risk}", markup=False, highlight=False)

    console.print(
        Rule(style=_phase_style("planning")),
        markup=False,
        highlight=False,
    )


def render_analysis_decision(
    workspace_root: Path,
    drain: str,
    console: Console,
) -> None:
    """Render an analysis decision artifact as a titled block."""
    artifact_type = _analysis_handoff_artifact_type(drain)
    if artifact_type is not None:
        markdown = _read_markdown_handoff(workspace_root, artifact_type)
        if markdown:
            _render_markdown_block(
                f"ANALYSIS: {drain}",
                markdown,
                "development_analysis",
                console,
            )
            return

    summary = read_latest_analysis_decision(workspace_root, drain)

    if summary is None:
        # Missing or malformed - no output per spec
        return

    console.print()
    console.print(
        Rule(f"ANALYSIS: {drain}", style=_phase_style("development_analysis")),
        markup=False,
        highlight=False,
    )

    console.print(f"  decision: {summary.decision}", markup=False, highlight=False)
    if summary.reason:
        console.print(f"  reason: {summary.reason}", markup=False, highlight=False)

    console.print(
        Rule(style=_phase_style("development_analysis")),
        markup=False,
        highlight=False,
    )


def render_commit_message(
    workspace_root: Path,
    console: Console,
) -> None:
    """Render the commit message artifact as a titled block.

    Reads ``.agent/tmp/commit_message.json`` (via commit_message module)
    and prints a titled Rule ``COMMIT MESSAGE`` followed by subject on its
    own line and body indented.
    Missing file produces no output; malformed JSON produces no output (defensive).
    """
    try:
        message = read_commit_message_artifact(workspace_root)
    except Exception:
        # Defensive: malformed artifact should not crash rendering
        message = None

    if message is None:
        # Missing or malformed - no output per spec
        return

    console.print()
    console.print(
        Rule("COMMIT MESSAGE", style=_phase_style("development_commit")),
        markup=False,
        highlight=False,
    )

    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        console.print(
            Rule(style=_phase_style("development_commit")),
            markup=False,
            highlight=False,
        )
        return

    console.print(f"  {lines[0]}", markup=False, highlight=False)
    for line in lines[1:]:
        console.print(f"    {line}", markup=False, highlight=False)

    console.print(
        Rule(style=_phase_style("development_commit")),
        markup=False,
        highlight=False,
    )


def _analysis_handoff_artifact_type(drain: str) -> str | None:
    mapping = {
        "development_analysis": "development_analysis_decision",
        "review_analysis": "review_analysis_decision",
    }
    return mapping.get(drain)


def render_review_artifact(
    workspace_root: Path,
    console: Console,
) -> None:
    """Render review findings using the Markdown handoff when available."""
    markdown = _read_markdown_handoff(workspace_root, "issues")
    if markdown:
        _render_markdown_block("REVIEW ISSUES", markdown, "review", console)
        return

    found = _read_json_defensive(workspace_root / _ARTIFACTS_DIR / "issues.json")
    if found is None:
        return
    _render_markdown_block("REVIEW ISSUES", json.dumps(found, indent=2), "review", console)


def render_fix_artifact(
    workspace_root: Path,
    console: Console,
) -> None:
    """Render fix result artifacts as a titled block."""
    markdown = _read_markdown_handoff(workspace_root, "fix_result")
    if markdown:
        _render_markdown_block("FIX", markdown, "fix", console)
        return

    found = _first_json_candidate(
        workspace_root / _ARTIFACTS_DIR / "fix_result.json",
        workspace_root / _ARTIFACTS_DIR / "issues.json",
    )
    if found is None:
        return

    console.print()
    console.print(Rule("FIX", style=_phase_style("fix")), markup=False, highlight=False)
    _render_fix_json_summary(found, console)
    console.print(Rule(style=_phase_style("fix")), markup=False, highlight=False)


def _first_json_candidate(*candidates: Path) -> dict[str, object] | None:
    for candidate in candidates:
        found = _read_json_defensive(candidate)
        if found is not None:
            return found
    return None


def _render_fix_json_summary(found: dict[str, object], console: Console) -> None:
    if "issues" in found and isinstance(found["issues"], list):
        _render_issues_summary(found["issues"], console)
        return
    if "fixed" in found:
        _render_fixed_summary(found["fixed"], console)
        return
    console.print(f"  Fix artifact: {list(found.keys())[:5]}", markup=False, highlight=False)


def _render_issues_summary(issues: list[object], console: Console) -> None:
    console.print(f"  {len(issues)} issue(s) addressed:", markup=False, highlight=False)
    for issue in issues[:10]:
        if isinstance(issue, dict):
            desc_obj = issue.get("description") or issue.get("message") or str(issue)
        else:
            desc_obj = str(issue)
        console.print(f"    - {str(desc_obj)[:120]}", markup=False, highlight=False)


def _render_fixed_summary(fixed: object, console: Console) -> None:
    if isinstance(fixed, list):
        console.print(f"  {len(fixed)} item(s) fixed:", markup=False, highlight=False)
        for item in fixed[:10]:
            console.print(f"    - {str(item)[:120]}", markup=False, highlight=False)
        return
    console.print(f"  Fixed: {fixed}", markup=False, highlight=False)


__all__ = [
    "render_analysis_decision",
    "render_commit_message",
    "render_fix_artifact",
    "render_plan_artifact",
    "render_review_artifact",
]
