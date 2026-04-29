"""End-of-run completion summary rendering for log-first output."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich.console import Group
from rich.rule import Rule
from rich.text import Text

from ralph.display.context import make_display_context
from ralph.display.phase_banner import _phase_style
from ralph.mcp.artifacts.commit_message import read_commit_message_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console

    from ralph.display.context import DisplayContext
    from ralph.display.snapshot import PipelineSnapshot

_VERIFICATION_ARTIFACT = ".agent/artifacts/verification.json"
_DECISION_LABELS: dict[str, str] = {
    "proceed": "PASS",
    "complete": "PASS",
    "pr_opened": "INFO",
    "revise": "WARN",
    "failed": "FAIL",
}

_BADGE_THEME_KEYS: dict[str, str] = {
    "PASS": "theme.status.success",
    "INFO": "theme.level.info",
    "WARN": "theme.level.warn",
    "FAIL": "theme.status.failure",
}


def _artifact_content(parsed: dict[str, object]) -> dict[str, object]:
    content = parsed.get("content")
    if isinstance(content, dict):
        return content
    return parsed


def _read_verification_status(workspace_root: Path | None) -> tuple[str, str | None]:
    if workspace_root is None:
        return ("unknown", None)
    path = workspace_root / _VERIFICATION_ARTIFACT
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, PermissionError):
        return ("unknown", None)
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return ("unknown", None)
    if not isinstance(parsed, dict):
        return ("unknown", None)
    parsed_dict = _artifact_content(parsed)
    status = parsed_dict.get("status") or parsed_dict.get("outcome")
    reason = parsed_dict.get("reason") or parsed_dict.get("summary") or parsed_dict.get("message")
    label = status if isinstance(status, str) and status else "unknown"
    reason_text = reason if isinstance(reason, str) and reason else None
    return (label, reason_text)


def _commit_message_lines(workspace_root: Path | None) -> list[str]:
    if workspace_root is None:
        return []
    message = read_commit_message_artifact(workspace_root)
    if message is None:
        return []

    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return []

    rendered = [f"Commit Message: {lines[0]}"]
    rendered.extend(f"  {line}" for line in lines[1:])
    return rendered


def _verification_line(workspace_root: Path | None) -> str:
    """Return a human-readable verification status line.

    Only reports a positive status when the verification artifact is present and
    readable. A missing or unreadable artifact yields 'not verified' — the
    pipeline's own phase/error state is not used as a proxy for verification.
    """
    status, reason = _read_verification_status(workspace_root)
    if status == "unknown":
        return "Verification: not verified"
    suffix = f" — {reason}" if reason else ""
    return f"Verification: {status}{suffix}"


def _dropped_count_line(dropped: int) -> str:
    """Return a line reporting dropped snapshots, shown only when drops occurred."""
    if dropped <= 0:
        return ""
    return f"Snapshots dropped: {dropped}"


def _make_badge_text(badge: str, rest: str) -> Text:
    """Build a Text object with a themed badge label followed by plain rest text."""
    theme_key = _BADGE_THEME_KEYS.get(badge, "theme.level.info")
    t = Text("  ")
    t.append(f"[{badge}]", style=theme_key)
    t.append(rest)
    return t


def render_completion_summary(  # noqa: PLR0913
    snapshot: PipelineSnapshot,
    *,
    workspace_root: Path | None = None,
    dropped_count: int = 0,
    content_block_count: int = 0,
    thinking_block_count: int = 0,
    tool_call_count: int = 0,
    error_count: int = 0,
    elapsed_seconds: float | None = None,
) -> Text:
    failed = snapshot.phase == "failed"
    lines: list[str] = ["Pipeline Failed" if failed else "Pipeline Complete"]

    if snapshot.plan_summary:
        lines.append(f"Plan: {snapshot.plan_summary}")
    if snapshot.plan_scope_items:
        lines.append(f"Scope: {len(snapshot.plan_scope_items)} item(s)")

    lines.append(
        "Metrics: "
        f"agent_calls={snapshot.total_agent_calls} "
        f"continuations={snapshot.total_continuations} "
        f"fallbacks={snapshot.total_fallbacks} "
        f"retries={snapshot.total_retries} "
        f"pushes={snapshot.push_count}"
    )

    activity_parts: list[str] = []
    if elapsed_seconds is not None:
        activity_parts.append(f"elapsed={round(elapsed_seconds, 1)}s")
    activity_parts.append(f"content_blocks={content_block_count}")
    activity_parts.append(f"thinking_blocks={thinking_block_count}")
    activity_parts.append(f"tool_calls={tool_call_count}")
    activity_parts.append(f"errors={error_count}")
    lines.append("Activity: " + " ".join(activity_parts))

    if snapshot.decision_log:
        lines.append("Decisions:")
        for phase, decision, reason, _ts in snapshot.decision_log:
            badge = _DECISION_LABELS.get(decision.lower(), "INFO")
            reason_part = f" — {reason}" if reason else ""
            lines.append(f"- [{badge}] {phase.replace('_', ' ').title()}: {decision}{reason_part}")
    else:
        lines.append("Decisions: (none recorded)")

    lines.append(_verification_line(workspace_root))
    lines.extend(_commit_message_lines(workspace_root))

    if snapshot.pr_url:
        lines.append(f"PR: {snapshot.pr_url}")
    if snapshot.last_error:
        lines.append(f"Error: {snapshot.last_error}")
    if snapshot.plan_risks:
        lines.append("Open Risks:")
        lines.extend(f"- {risk}" for risk in snapshot.plan_risks)

    dropped_line = _dropped_count_line(dropped_count)
    if dropped_line:
        lines.append(dropped_line)

    return Text("\n".join(lines))


def _render_compact_group(  # noqa: PLR0912, PLR0913
    snapshot: PipelineSnapshot,
    *,
    workspace_root: Path | None = None,
    dropped_count: int = 0,
    thinking_block_count: int = 0,
    overflow_path: str | None = None,
    content_block_count: int = 0,
    tool_call_count: int = 0,
    error_count: int = 0,
    elapsed_seconds: float | None = None,
) -> Group:
    """Compact single-column layout: section tags replace Rule headers."""
    failed = snapshot.phase == "failed"
    style = _phase_style("failed" if failed else "complete")
    title = "Pipeline Failed" if failed else "Pipeline Complete"

    renderables: list[Text] = [Text(title, style=style)]

    if snapshot.plan_summary or snapshot.plan_scope_items:
        if snapshot.plan_summary:
            renderables.append(Text(f"PLAN: {snapshot.plan_summary}"))
        if snapshot.plan_scope_items:
            renderables.append(Text(f"PLAN: Scope: {len(snapshot.plan_scope_items)} item(s)"))

    renderables.append(
        Text(
            f"METRICS: agent_calls={snapshot.total_agent_calls} "
            f"continuations={snapshot.total_continuations} "
            f"fallbacks={snapshot.total_fallbacks} "
            f"retries={snapshot.total_retries} "
            f"pushes={snapshot.push_count}"
        )
    )

    if snapshot.decision_log:
        for phase, decision, reason, _ts in snapshot.decision_log:
            badge = _DECISION_LABELS.get(decision.lower(), "INFO")
            reason_part = f": {decision}" + (f" — {reason}" if reason else "")
            phase_title = phase.replace('_', ' ').title()
            renderables.append(
                _make_badge_text(badge, f" DECISIONS: {phase_title}{reason_part}")
            )
    else:
        renderables.append(Text("DECISIONS: (none recorded)"))

    renderables.append(Text(f"VERIFICATION: {_verification_line(workspace_root)}"))

    activity_parts: list[str] = []
    if elapsed_seconds is not None:
        activity_parts.append(f"elapsed={round(elapsed_seconds, 1)}s")
    activity_parts.extend([
        f"agent_calls={snapshot.total_agent_calls}",
        f"content_blocks={content_block_count}",
        f"thinking_blocks={thinking_block_count}",
        f"tool_calls={tool_call_count}",
        f"errors={error_count}",
    ])
    if overflow_path is not None:
        activity_parts.append(f"raw_overflow={overflow_path}")
    renderables.append(Text("ACTIVITY: " + " ".join(activity_parts)))

    commit_lines = _commit_message_lines(workspace_root)
    if commit_lines or snapshot.pr_url:
        renderables.extend(Text(f"COMMIT: {ln}") for ln in commit_lines)
        if snapshot.pr_url:
            renderables.append(Text(f"COMMIT: PR: {snapshot.pr_url}"))

    if snapshot.plan_risks:
        renderables.extend(Text(f"RISKS: - {risk}") for risk in snapshot.plan_risks)

    if snapshot.last_error:
        renderables.append(Text(f"ERROR: {snapshot.last_error}"))

    dropped_line = _dropped_count_line(dropped_count)
    if dropped_line:
        renderables.append(Text(f"  {dropped_line}"))

    return Group(*renderables)


def render_completion_summary_group(  # noqa: PLR0912, PLR0913
    snapshot: PipelineSnapshot,
    *,
    workspace_root: Path | None = None,
    dropped_count: int = 0,
    thinking_block_count: int = 0,
    overflow_path: str | None = None,
    content_block_count: int = 0,
    tool_call_count: int = 0,
    error_count: int = 0,
    elapsed_seconds: float | None = None,
    context: DisplayContext | None = None,
) -> Group:
    """Render the completion summary as a Rich Group with rule-delimited sections.

    In compact mode the section Rule headers are replaced with uppercase tag prefixes.
    Returns a Group suitable for ``console.print(group, markup=False, highlight=False)``.
    """
    if context is not None and context.mode == "compact":
        return _render_compact_group(
            snapshot,
            workspace_root=workspace_root,
            dropped_count=dropped_count,
            thinking_block_count=thinking_block_count,
            overflow_path=overflow_path,
            content_block_count=content_block_count,
            tool_call_count=tool_call_count,
            error_count=error_count,
            elapsed_seconds=elapsed_seconds,
        )

    failed = snapshot.phase == "failed"
    style = _phase_style("failed" if failed else "complete")
    title = "Pipeline Failed" if failed else "Pipeline Complete"

    renderables: list[Rule | Text] = []

    # Header rule
    renderables.append(Rule(title, style=style))

    # Plan section
    if snapshot.plan_summary or snapshot.plan_scope_items:
        renderables.append(Rule("Plan", style=_phase_style("planning")))
        if snapshot.plan_summary:
            renderables.append(Text(f"  {snapshot.plan_summary}"))
        if snapshot.plan_scope_items:
            renderables.append(Text(f"  Scope: {len(snapshot.plan_scope_items)} item(s)"))

    # Metrics section
    renderables.append(Rule("Metrics", style=style))
    renderables.append(
        Text(
            f"  agent_calls={snapshot.total_agent_calls} "
            f"continuations={snapshot.total_continuations} "
            f"fallbacks={snapshot.total_fallbacks} "
            f"retries={snapshot.total_retries} "
            f"pushes={snapshot.push_count}"
        )
    )

    # Decisions section
    renderables.append(Rule("Decisions", style=style))
    if snapshot.decision_log:
        for phase, decision, reason, _ts in snapshot.decision_log:
            badge = _DECISION_LABELS.get(decision.lower(), "INFO")
            reason_part = f": {decision}" + (f" — {reason}" if reason else "")
            renderables.append(
                _make_badge_text(
                    badge,
                    f" {phase.replace('_', ' ').title()}{reason_part}",
                )
            )
    else:
        renderables.append(Text("  (none recorded)"))

    # Verification section
    renderables.append(Rule("Verification", style=style))
    renderables.append(Text(f"  {_verification_line(workspace_root)}"))

    # Activity Summary section
    renderables.append(Rule("Activity Summary", style=style))
    if elapsed_seconds is not None:
        renderables.append(Text(f"  elapsed={round(elapsed_seconds, 1)}s"))
    renderables.append(Text(f"  agent_calls={snapshot.total_agent_calls}"))
    renderables.append(Text(f"  content_blocks={content_block_count}"))
    renderables.append(Text(f"  thinking_blocks={thinking_block_count}"))
    renderables.append(Text(f"  tool_calls={tool_call_count}"))
    renderables.append(Text(f"  errors={error_count}"))
    if overflow_path is not None:
        renderables.append(Text(f"  raw_overflow={overflow_path}"))

    # Commit section
    commit_lines = _commit_message_lines(workspace_root)
    if commit_lines or snapshot.pr_url:
        renderables.append(Rule("Commit", style=_phase_style("development_commit")))
        renderables.extend(Text(f"  {ln}") for ln in commit_lines)
        if snapshot.pr_url:
            renderables.append(Text(f"  PR: {snapshot.pr_url}"))

    # Risks section
    if snapshot.plan_risks:
        renderables.append(Rule("Open Risks", style=_phase_style("fix")))
        renderables.extend(Text(f"  - {risk}") for risk in snapshot.plan_risks)

    # Error section
    if snapshot.last_error:
        renderables.append(Rule("Error", style=_phase_style("failed")))
        renderables.append(Text(f"  {snapshot.last_error}"))

    dropped_line = _dropped_count_line(dropped_count)
    if dropped_line:
        renderables.append(Text(f"  {dropped_line}"))

    # Footer rule
    renderables.append(Rule(style=style))

    return Group(*renderables)


def emit_completion_summary(  # noqa: PLR0913
    console: Console,
    snapshot: PipelineSnapshot,
    *,
    workspace_root: Path | None = None,
    dropped_count: int = 0,
    thinking_block_count: int = 0,
    overflow_path: str | None = None,
    content_block_count: int = 0,
    tool_call_count: int = 0,
    error_count: int = 0,
    elapsed_seconds: float | None = None,
    context: DisplayContext | None = None,
) -> None:
    if context is None:
        context = make_display_context(console=console)
    console.print(
        render_completion_summary_group(
            snapshot,
            workspace_root=workspace_root,
            dropped_count=dropped_count,
            thinking_block_count=thinking_block_count,
            overflow_path=overflow_path,
            content_block_count=content_block_count,
            tool_call_count=tool_call_count,
            error_count=error_count,
            elapsed_seconds=elapsed_seconds,
            context=context,
        ),
        markup=False,
        highlight=False,
    )


__all__ = [
    "emit_completion_summary",
    "render_completion_summary",
    "render_completion_summary_group",
]
