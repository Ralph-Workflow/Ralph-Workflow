"""End-of-run completion summary rendering for log-first output."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from rich.console import Group
from rich.rule import Rule
from rich.text import Text

from ralph.display.phase_banner import _phase_style
from ralph.display.phase_status import (
    format_dev_cycle,
    format_elapsed_seconds,
    format_exit_trigger,
)
from ralph.mcp.artifacts.commit_message import read_commit_message_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.context import DisplayContext
    from ralph.display.snapshot import PipelineSnapshot
    from ralph.policy.models import PipelinePolicy


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

_CHILDREN_PERSIST_MARKER = "kept child agents alive"
_KV_PATTERN = re.compile(r"(\w+)=([^,)\s]+)")


def _children_persist_diagnostic_line(error: str) -> str | None:
    """Parse a CHILDREN_PERSIST_TOO_LONG error string into a human-readable reason line.

    Returns None when the error does not match the marker phrase.
    Missing keys in the diagnostic render as '?'.
    """
    if _CHILDREN_PERSIST_MARKER not in error:
        return None
    pairs: dict[str, str] = {m.group(1): m.group(2) for m in _KV_PATTERN.finditer(error)}
    cum = pairs.get("cumulative", "?")
    scoped = pairs.get("scoped_child_active", "?")
    oldest = pairs.get("oldest_child_seconds", "?")
    delta = pairs.get("workspace_event_delta", "?")
    evidence = pairs.get("evidence", "?")
    return (
        f"Reason: long child wait — cumulative={cum}, scoped_child_active={scoped},"
        f" oldest_child_seconds={oldest}, workspace_event_delta={delta}, evidence={evidence}"
    )


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
    readable. A missing or unreadable artifact yields 'not verified' \u2014 the
    pipeline's own phase/error state is not used as a proxy for verification.
    """
    status, reason = _read_verification_status(workspace_root)
    if status == "unknown":
        return "Verification: not verified"
    suffix = f" \u2014 {reason}" if reason else ""
    return f"Verification: {status}{suffix}"


def _debug_breadcrumb_lines(snapshot: PipelineSnapshot) -> list[str]:
    """Return debug breadcrumb lines for last activity, waiting status, and failure category."""
    lines = []
    if snapshot.last_activity_line:
        lines.append(f"last_activity: {snapshot.last_activity_line}")
    if snapshot.waiting_status_line:
        lines.append(f"waiting: {snapshot.waiting_status_line}")
    if snapshot.last_failure_category:
        lines.append(f"failure_category: {snapshot.last_failure_category}")
    return lines


def _dropped_count_line(dropped: int) -> str:
    """Return a line reporting dropped snapshots, shown only when drops occurred."""
    if dropped <= 0:
        return ""
    return f"Snapshots dropped: {dropped}"


def _review_summary_line(snapshot: PipelineSnapshot) -> tuple[str, str] | None:
    """Return (badge, summary) for review section based on review_issues_found and decision_log.

    Returns None when no review phase is in the decision log.
    Badge is 'PASS', 'FAIL', or 'INFO'.
    """
    if snapshot.review_issues_found:
        return ("FAIL", "issues found")
    has_review = snapshot.decision_log and any(
        "review" in phase.lower() for phase, _, _, _ in snapshot.decision_log
    )
    if has_review:
        return ("PASS", "clean")
    return None


def _review_badge_and_count(snapshot: PipelineSnapshot) -> tuple[str, int] | None:
    """Return (badge, issue_count) for review section.

    Returns None when no review phase is in the decision log.
    Badge is 'PASS', 'FAIL', or 'INFO'.
    """
    if snapshot.review_issues_found:
        return ("FAIL", 1)
    has_review = snapshot.decision_log and any(
        "review" in phase.lower() for phase, _, _, _ in snapshot.decision_log
    )
    if has_review:
        return ("PASS", 0)
    return None


def _analysis_decision_summary(
    snapshot: PipelineSnapshot,
) -> list[tuple[str, str, str]]:
    """Return list of (phase, decision, reason) for analysis decisions.

    Shows analysis decisions with proceed/revise labeling for clarity.
    """
    if not snapshot.decision_log:
        return []
    results: list[tuple[str, str, str]] = []
    for phase, decision, reason, _ts in snapshot.decision_log:
        if "analysis" in phase.lower():
            # Normalize decision to proceed/revise
            normalized = decision.lower().strip()
            if normalized in ("proceed", "complete", "pr_opened"):
                label = "proceed"
            elif normalized in ("revise", "failed"):
                label = "revise"
            else:
                label = decision
            results.append((phase, label, reason))
    return results


def _exit_trigger_label(snapshot: PipelineSnapshot) -> str:
    """Return a human-readable exit trigger label derived from snapshot state."""
    return format_exit_trigger(snapshot)


def _has_iteration_context(snapshot: PipelineSnapshot) -> bool:
    """Return True when any iteration context field is populated."""
    return snapshot.outer_dev_iteration is not None


def _budget_progress_lines(snapshot: PipelineSnapshot) -> list[tuple[str, int, int]]:
    """Return (description, completed, cap) for budget-tracked counters with a cap > 0."""
    return [
        (bp.description, bp.completed, bp.cap)
        for bp in snapshot.budget_progress.values()
        if bp.tracks_budget and bp.cap > 0
    ]


def _iteration_context_lines(snapshot: PipelineSnapshot) -> list[str]:
    """Return display lines for the iteration context section.

    Shows outer dev cycle when set, including total budget cap when available.
    Returns an empty list when no context is available.
    """
    if snapshot.outer_dev_iteration is not None:
        cap = next(
            (bp.cap for bp in snapshot.budget_progress.values() if bp.tracks_budget),
            None,
        )
        return [format_dev_cycle(snapshot.outer_dev_iteration, cap)]
    return []


def _style_for_role(
    role: str,
    pipeline_policy: PipelinePolicy | None,
) -> str:
    """Return the style for the first phase with the given role, or muted when none matches."""
    if pipeline_policy is not None:
        for phase_name, phase_def in pipeline_policy.phases.items():
            if phase_def.role == role:
                return _phase_style(phase_name, pipeline_policy)
    return "theme.text.muted"


def _style_for_terminal_failure(
    pipeline_policy: PipelinePolicy | None,
) -> str:
    """Return the style for the terminal failure phase, or the failed theme default."""
    if pipeline_policy is not None:
        for phase_name, phase_def in pipeline_policy.phases.items():
            if phase_def.role == "terminal" and phase_def.terminal_outcome == "failure":
                return _phase_style(phase_name, pipeline_policy)
    return "theme.phase.failed"


def _make_badge_text(badge: str, rest: str) -> Text:
    """Build a Text object with a themed badge label followed by muted rest text."""
    theme_key = _BADGE_THEME_KEYS.get(badge, "theme.level.info")
    t = Text("  ")
    t.append(f"[{badge}]", style=theme_key)
    t.append(rest, style="theme.text.muted")
    return t


def render_completion_summary(  # noqa: PLR0913, PLR0912, PLR0915
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
    failed = snapshot.is_terminal_failure
    lines: list[str] = ["Pipeline Failed" if failed else "Pipeline Complete"]

    lines.append(f"Exit: {_exit_trigger_label(snapshot)}")

    if elapsed_seconds is not None:
        lines.append(f"Elapsed: {format_elapsed_seconds(elapsed_seconds)}")

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
            reason_part = f" \u2014 {reason}" if reason else ""
            lines.append(f"- [{badge}] {phase.replace('_', ' ').title()}: {decision}{reason_part}")
    else:
        lines.append("Decisions: (none recorded)")

    review_line = _review_summary_line(snapshot)
    if review_line is not None:
        badge, summary_text = review_line
        lines.append(f"Review: [{badge}] {summary_text}")

    # Analysis decisions with proceed/revise labeling
    analysis_decisions = _analysis_decision_summary(snapshot)
    if analysis_decisions:
        lines.append("Analysis Decisions:")
        for phase, decision, reason in analysis_decisions:
            reason_part = f" — {reason}" if reason else ""
            lines.append(f"- {phase.replace('_', ' ').title()}: {decision}{reason_part}")

    # Iteration context (outer dev + fixer)
    iter_lines = _iteration_context_lines(snapshot)
    if iter_lines:
        lines.append("Iteration Context:")
        lines.extend(f"  {ln}" for ln in iter_lines)

    # Budget progress (shows dev-cycle budget consumed vs cap)
    budget_lines = _budget_progress_lines(snapshot)
    if budget_lines:
        lines.append("Budget Progress:")
        for desc, completed, cap in budget_lines:
            remaining = cap - completed
            lines.append(f"  {desc}: {completed}/{cap} used, {remaining} remaining")

    lines.append(_verification_line(workspace_root))
    lines.extend(_commit_message_lines(workspace_root))

    if snapshot.pr_url:
        lines.append(f"PR: {snapshot.pr_url}")
    if snapshot.last_error:
        lines.append(f"Error: {snapshot.last_error}")
        diag = _children_persist_diagnostic_line(snapshot.last_error)
        if diag:
            lines.append(diag)
    if snapshot.plan_risks:
        lines.append("Open Risks:")
        lines.extend(f"- {risk}" for risk in snapshot.plan_risks)

    debug_lines = _debug_breadcrumb_lines(snapshot)
    if debug_lines:
        lines.append("Debug:")
        lines.extend(f"  {ln}" for ln in debug_lines)

    dropped_line = _dropped_count_line(dropped_count)
    if dropped_line:
        lines.append(dropped_line)

    return Text("\n".join(lines))


def _render_compact_group(  # noqa: PLR0912, PLR0913, PLR0915
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
    include_context_sections: bool = True,
    pipeline_policy: PipelinePolicy | None = None,
) -> Group:
    """Compact single-column layout: section tags replace Rule headers."""
    failed = snapshot.is_terminal_failure
    if failed:
        style = _style_for_terminal_failure(pipeline_policy)
    else:
        style = _style_for_role("terminal", pipeline_policy)
    title = "Pipeline Failed" if failed else "Pipeline Complete"
    title_with_elapsed = (
        f"{title}  elapsed={format_elapsed_seconds(elapsed_seconds)}"
        if elapsed_seconds is not None
        else title
    )

    renderables: list[Text] = [Text(title_with_elapsed, style=style)]
    renderables.append(Text(f"EXIT: {_exit_trigger_label(snapshot)}"))

    if include_context_sections and (snapshot.plan_summary or snapshot.plan_scope_items):
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
            reason_part = f": {decision}" + (f" \u2014 {reason}" if reason else "")
            phase_title = phase.replace('_', ' ').title()
            renderables.append(
                _make_badge_text(badge, f" DECISIONS: {phase_title}{reason_part}")
            )
    else:
        renderables.append(Text("DECISIONS: (none recorded)"))

    review_line = _review_summary_line(snapshot)
    if review_line is not None:
        badge, summary_text = review_line
        renderables.append(Text(f"REVIEW: [{badge}] {summary_text}".upper()))

    # Analysis decisions with proceed/revise labeling in compact mode
    analysis_decisions = _analysis_decision_summary(snapshot)
    if analysis_decisions:
        for phase, decision, reason in analysis_decisions:
            reason_part = f" — {reason}" if reason else ""
            analysis_line = f"ANALYSIS: {phase.replace('_', ' ').title()}: {decision}{reason_part}"
            renderables.append(Text(analysis_line.upper()))

    # Iteration context (outer dev + fixer) in compact mode
    iter_lines = _iteration_context_lines(snapshot)
    if iter_lines:
        renderables.append(Text(f"CONTEXT: {' | '.join(iter_lines)}"))

    # Budget progress in compact mode
    budget_lines = _budget_progress_lines(snapshot)
    if budget_lines:
        for desc, completed, cap in budget_lines:
            remaining = cap - completed
            renderables.append(Text(f"BUDGET: {desc}: {completed}/{cap} ({remaining} left)"))

    renderables.append(Text(f"VERIFICATION: {_verification_line(workspace_root)}"))

    activity_parts: list[str] = [
        f"agent_calls={snapshot.total_agent_calls}",
        f"content_blocks={content_block_count}",
        f"thinking_blocks={thinking_block_count}",
        f"tool_calls={tool_call_count}",
        f"errors={error_count}",
    ]
    if overflow_path is not None:
        activity_parts.append(f"raw_overflow={overflow_path}")
    renderables.append(Text("ACTIVITY: " + " ".join(activity_parts)))

    commit_lines = _commit_message_lines(workspace_root)
    if commit_lines or snapshot.pr_url:
        renderables.extend(Text(f"COMMIT: {ln}") for ln in commit_lines)
        if snapshot.pr_url:
            renderables.append(Text(f"COMMIT: PR: {snapshot.pr_url}"))

    if include_context_sections and snapshot.plan_risks:
        renderables.extend(Text(f"RISKS: - {risk}") for risk in snapshot.plan_risks)

    if snapshot.last_error:
        renderables.append(Text(f"ERROR: {snapshot.last_error}"))
        diag = _children_persist_diagnostic_line(snapshot.last_error)
        if diag:
            renderables.append(Text(f"REASON: {diag}"))

    # Debug breadcrumbs in compact mode
    renderables.extend(Text(f"DEBUG: {ln}") for ln in _debug_breadcrumb_lines(snapshot))

    dropped_line = _dropped_count_line(dropped_count)
    if dropped_line:
        renderables.append(Text(f"  {dropped_line}"))

    return Group(*renderables)


def render_completion_summary_group(  # noqa: PLR0912, PLR0913, PLR0915
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
    include_context_sections: bool = True,
    display_context: DisplayContext,
    pipeline_policy: PipelinePolicy | None = None,
) -> Group:
    """Render the completion summary as a Rich Group with rule-delimited sections.

    In compact mode the section Rule headers are replaced with uppercase tag prefixes.
    Returns a Group suitable for ``console.print(group, markup=False, highlight=False)``.

    Args:
        snapshot: Pipeline snapshot with run metadata.
        workspace_root: Path to workspace for artifact reading.
        dropped_count: Number of dropped snapshots.
        thinking_block_count: Number of thinking blocks.
        overflow_path: Path to overflow log if any.
        content_block_count: Number of content blocks.
        tool_call_count: Number of tool calls.
        error_count: Number of errors.
        elapsed_seconds: Total elapsed time.
        display_context: DisplayContext providing console and mode.
    """
    if display_context.mode == "compact":
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
            include_context_sections=include_context_sections,
            pipeline_policy=pipeline_policy,
        )

    failed = snapshot.is_terminal_failure
    if failed:
        style = _style_for_terminal_failure(pipeline_policy)
    else:
        style = _style_for_role("terminal", pipeline_policy)
    title = "Pipeline Failed" if failed else "Pipeline Complete"

    renderables: list[Rule | Text] = []

    # Header rule
    renderables.append(Rule(title, style=style))

    # Exit trigger and elapsed — shown immediately after header for quick orientation
    renderables.append(Text(f"  exit={_exit_trigger_label(snapshot)}"))
    if elapsed_seconds is not None:
        renderables.append(Text(f"  elapsed={format_elapsed_seconds(elapsed_seconds)}"))

    # Plan section
    if include_context_sections and (snapshot.plan_summary or snapshot.plan_scope_items):
        plan_style = _style_for_role("execution", pipeline_policy)
        renderables.append(Rule("Plan", style=plan_style))
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
            reason_part = f": {decision}" + (f" \u2014 {reason}" if reason else "")
            renderables.append(
                _make_badge_text(
                    badge,
                    f" {phase.replace('_', ' ').title()}{reason_part}",
                )
            )
    else:
        renderables.append(Text("  (none recorded)"))

    # Enhanced Review section with PASS/FAIL badge and issue count
    review_info = _review_badge_and_count(snapshot)
    if review_info is not None:
        badge, issue_count = review_info
        review_style = _style_for_role("review", pipeline_policy)
        renderables.append(Rule("Review", style=review_style))
        count_suffix = f" ({issue_count} issue(s))" if issue_count > 0 else " (clean)"
        renderables.append(_make_badge_text(badge, f"{count_suffix}"))

    # Analysis Decisions section with proceed/revise labeling
    analysis_decisions = _analysis_decision_summary(snapshot)
    if analysis_decisions:
        analysis_style = _style_for_role("analysis", pipeline_policy) if pipeline_policy else style
        renderables.append(Rule("Analysis Decisions", style=analysis_style))
        for phase, decision, reason in analysis_decisions:
            reason_part = f": {decision}" + (f" — {reason}" if reason else "")
            phase_title = phase.replace('_', ' ').title()
            # Determine badge based on decision
            if decision == "proceed":
                decision_badge = "PASS"
            elif decision == "revise":
                decision_badge = "WARN"
            else:
                decision_badge = "INFO"
            renderables.append(
                _make_badge_text(decision_badge, f" {phase_title}{reason_part}")
            )

    # Iteration Context section (outer dev cycle)
    if _has_iteration_context(snapshot):
        renderables.append(Rule("Iteration Context", style=style))
        renderables.extend(Text(f"  {ln}") for ln in _iteration_context_lines(snapshot))

    # Budget Progress section (dev-cycle budget consumed vs cap)
    budget_lines = _budget_progress_lines(snapshot)
    if budget_lines:
        budget_style = _style_for_role("execution", pipeline_policy) if pipeline_policy else style
        renderables.append(Rule("Budget Progress", style=budget_style))
        for desc, completed, cap in budget_lines:
            remaining = cap - completed
            renderables.append(Text(f"  {desc}: {completed}/{cap} used, {remaining} remaining"))

    # Activity Summary section — before Verification so timing context precedes status
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

    # Verification section
    renderables.append(Rule("Verification", style=style))
    renderables.append(Text(f"  {_verification_line(workspace_root)}"))

    # Commit section
    commit_lines = _commit_message_lines(workspace_root)
    if commit_lines or snapshot.pr_url:
        commit_style = _style_for_role("commit", pipeline_policy)
        renderables.append(Rule("Commit", style=commit_style))
        renderables.extend(Text(f"  {ln}") for ln in commit_lines)
        if snapshot.pr_url:
            renderables.append(Text(f"  PR: {snapshot.pr_url}"))

    # Risks section
    if include_context_sections and snapshot.plan_risks:
        renderables.append(Rule("Open Risks", style=_style_for_role("fix", pipeline_policy)))
        renderables.extend(Text(f"  - {risk}") for risk in snapshot.plan_risks)

    # Error section
    if snapshot.last_error:
        renderables.append(Rule("Error", style=_style_for_terminal_failure(pipeline_policy)))
        renderables.append(Text(f"  {snapshot.last_error}"))
        diag = _children_persist_diagnostic_line(snapshot.last_error)
        if diag:
            renderables.append(Text(f"  {diag}"))

    dropped_line = _dropped_count_line(dropped_count)
    if dropped_line:
        renderables.append(Text(f"  {dropped_line}"))

    # Debug breadcrumbs last — diagnostic info for post-mortem investigation
    breadcrumb_lines = _debug_breadcrumb_lines(snapshot)
    if breadcrumb_lines:
        renderables.append(Rule("Debug", style="theme.text.muted"))
        renderables.extend(Text(f"  {ln}") for ln in breadcrumb_lines)

    # Footer rule
    renderables.append(Rule(style=style))

    return Group(*renderables)


def emit_completion_summary(  # noqa: PLR0913
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
    include_context_sections: bool = True,
    display_context: DisplayContext,
    pipeline_policy: PipelinePolicy | None = None,
) -> None:
    """Emit the completion summary to the console.

    Args:
        snapshot: Pipeline snapshot with run metadata.
        workspace_root: Path to workspace for artifact reading.
        dropped_count: Number of dropped snapshots.
        thinking_block_count: Number of thinking blocks.
        overflow_path: Path to overflow log if any.
        content_block_count: Number of content blocks.
        tool_call_count: Number of tool calls.
        error_count: Number of errors.
        elapsed_seconds: Total elapsed time.
        display_context: DisplayContext providing the console and mode.
        pipeline_policy: Optional policy for role-based section styling.
    """
    console = display_context.console
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
            include_context_sections=include_context_sections,
            display_context=display_context,
            pipeline_policy=pipeline_policy,
        ),
        markup=False,
        highlight=False,
    )


__all__ = [
    "emit_completion_summary",
    "render_completion_summary",
    "render_completion_summary_group",
]
