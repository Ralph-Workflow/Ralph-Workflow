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

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console


_ARTIFACTS_DIR = ".agent/artifacts"


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
    """Render the plan.json artifact as a titled, well-formatted block.

    Reads ``.agent/artifacts/plan.json`` and prints a titled Rule followed by
    summary context, bulleted scope items, step count, and risks.
    Missing file produces no output; malformed JSON prints a single error line.
    """
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
    """Render an analysis decision artifact as a titled block.

    Reads ``{drain}_decision.json`` or ``{drain}.json`` from artifacts dir
    and prints a titled Rule ``ANALYSIS: <drain>`` followed by decision and reason.
    Missing file produces no output; malformed JSON prints a single error line.
    """
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


def render_fix_artifact(
    workspace_root: Path,
    console: Console,
) -> None:
    """Render fix result artifacts as a titled block.

    Reads ``.agent/artifacts/issues.json`` or ``.agent/artifacts/fix_result.json``
    if present and prints a short bullet list. Missing files produce no output;
    malformed JSON prints a single error line.
    """
    candidates = [
        workspace_root / _ARTIFACTS_DIR / "issues.json",
        workspace_root / _ARTIFACTS_DIR / "fix_result.json",
    ]

    found: dict[str, object] | None = None
    for candidate in candidates:
        found = _read_json_defensive(candidate)
        if found is not None:
            break

    if found is None:
        # No file present - no output per spec
        return

    console.print()
    console.print(
        Rule("FIX", style=_phase_style("fix")),
        markup=False,
        highlight=False,
    )

    # Extract a summary from the artifact
    if "issues" in found and isinstance(found["issues"], list):
        issues = found["issues"]
        console.print(
            f"  {len(issues)} issue(s) addressed:",
            markup=False,
            highlight=False,
        )
        for issue in issues[:10]:  # Cap at 10 for transcript safety
            if isinstance(issue, dict):
                desc_obj = issue.get("description") or issue.get("message") or str(issue)
            else:
                desc_obj = str(issue)
            desc = str(desc_obj)
            console.print(f"    - {desc[:120]}", markup=False, highlight=False)
    elif "fixed" in found:
        fixed = found["fixed"]
        if isinstance(fixed, list):
            console.print(
                f"  {len(fixed)} item(s) fixed:",
                markup=False,
                highlight=False,
            )
            for item in fixed[:10]:
                console.print(f"    - {str(item)[:120]}", markup=False, highlight=False)
        else:
            console.print(f"  Fixed: {fixed}", markup=False, highlight=False)
    else:
        # Generic fallback - just print keys
        console.print(
            f"  Fix artifact: {list(found.keys())[:5]}",
            markup=False,
            highlight=False,
        )

    console.print(
        Rule(style=_phase_style("fix")),
        markup=False,
        highlight=False,
    )


__all__ = [
    "render_analysis_decision",
    "render_commit_message",
    "render_fix_artifact",
    "render_plan_artifact",
]
