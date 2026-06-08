"""Grouped Rich rendering helpers for completion summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.rule import Rule
from rich.text import Text

from ralph.display._decision_labels import DECISION_BADGE_MAP as _DECISION_LABELS
from ralph.display.completion_summary import (
    _analysis_decision_summary,
    _children_persist_diagnostic_line,
    _commit_message_lines,
    _debug_breadcrumb_lines,
    _dropped_count_line,
    _exit_trigger_label,
    _has_iteration_context,
    _iteration_context_lines,
    _review_badge_and_count,
    _review_summary_line,
    _verification_line,
    analysis_decision_badge,
    make_badge_text,
    style_for_role,
    style_for_terminal_failure,
)
from ralph.display.phase_status import format_elapsed_seconds

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.completion_summary import CompletionSummaryOptions
    from ralph.display.context import DisplayContext
    from ralph.display.snapshot import PipelineSnapshot
    from ralph.policy.models import PipelinePolicy


def _compact_decisions_items(snapshot: PipelineSnapshot) -> list[Text]:
    if not snapshot.decision_log:
        return [Text("DECISIONS: (none recorded)")]
    items: list[Text] = []
    for phase, decision, reason, _ts in snapshot.decision_log:
        badge = _DECISION_LABELS.get(decision.lower(), "INFO")
        reason_part = f": {decision}" + (f" — {reason}" if reason else "")
        phase_title = phase.replace("_", " ").title()
        items.append(make_badge_text(badge, f" DECISIONS: {phase_title}{reason_part}"))
    return items


def _compact_analysis_items(snapshot: PipelineSnapshot) -> list[Text]:
    analysis_decisions = _analysis_decision_summary(snapshot)
    if not analysis_decisions:
        return []
    items: list[Text] = []
    for phase, decision, reason in analysis_decisions:
        reason_part = f" — {reason}" if reason else ""
        line = f"ANALYSIS: {phase.replace('_', ' ').title()}: {decision}{reason_part}"
        items.append(Text(line.upper()))
    return items


def _compact_tail_items(
    snapshot: PipelineSnapshot,
    workspace_root: Path | None,
    dropped_count: int,
    include_context_sections: bool,
) -> list[Text]:
    items: list[Text] = []
    commit_lines = _commit_message_lines(workspace_root)
    if commit_lines or snapshot.pr_url:
        items.extend(Text(f"COMMIT: {line}") for line in commit_lines)
        if snapshot.pr_url:
            items.append(Text(f"COMMIT: PR: {snapshot.pr_url}"))
    if include_context_sections and snapshot.plan_risks:
        items.extend(Text(f"RISKS: - {risk}") for risk in snapshot.plan_risks)
    if snapshot.last_error:
        items.append(Text(f"ERROR: {snapshot.last_error}"))
        diag = _children_persist_diagnostic_line(snapshot.last_error)
        if diag:
            items.append(Text(f"REASON: {diag}"))
    items.extend(Text(f"DEBUG: {line}") for line in _debug_breadcrumb_lines(snapshot))
    dropped_line = _dropped_count_line(dropped_count)
    if dropped_line:
        items.append(Text(f"  {dropped_line}"))
    return items


def _wide_plan_section(
    snapshot: PipelineSnapshot,
    pipeline_policy: PipelinePolicy | None,
    include_context_sections: bool,
) -> list[Rule | Text]:
    if not include_context_sections:
        return []
    if not (snapshot.plan_summary or snapshot.plan_scope_items):
        return []
    plan_style = style_for_role("execution", pipeline_policy)
    items: list[Rule | Text] = [Rule("Plan", style=plan_style)]
    if snapshot.plan_summary:
        items.append(Text(f"  {snapshot.plan_summary}"))
    if snapshot.plan_scope_items:
        items.append(Text(f"  Scope: {len(snapshot.plan_scope_items)} item(s)"))
    return items


def _wide_decisions_section(
    snapshot: PipelineSnapshot,
    style: str,
) -> list[Rule | Text]:
    items: list[Rule | Text] = [Rule("Decisions", style=style)]
    if snapshot.decision_log:
        for phase, decision, reason, _ts in snapshot.decision_log:
            badge = _DECISION_LABELS.get(decision.lower(), "INFO")
            reason_part = f": {decision}" + (f" — {reason}" if reason else "")
            items.append(make_badge_text(badge, f" {phase.replace('_', ' ').title()}{reason_part}"))
    else:
        items.append(Text("  (none recorded)"))
    return items


def _wide_review_section(
    snapshot: PipelineSnapshot,
    pipeline_policy: PipelinePolicy | None,
) -> list[Rule | Text]:
    review_info = _review_badge_and_count(snapshot)
    if review_info is None:
        return []
    badge, issue_count = review_info
    review_style = style_for_role("review", pipeline_policy)
    count_suffix = f" ({issue_count} issue(s))" if issue_count > 0 else " (clean)"
    return [Rule("Review", style=review_style), make_badge_text(badge, count_suffix)]


def _wide_analysis_section(
    snapshot: PipelineSnapshot,
    pipeline_policy: PipelinePolicy | None,
    style: str,
) -> list[Rule | Text]:
    analysis_decisions = _analysis_decision_summary(snapshot)
    if not analysis_decisions:
        return []
    analysis_style = style_for_role("analysis", pipeline_policy) if pipeline_policy else style
    items: list[Rule | Text] = [Rule("Analysis Decisions", style=analysis_style)]
    for phase, decision, reason in analysis_decisions:
        reason_part = f": {decision}" + (f" — {reason}" if reason else "")
        phase_title = phase.replace("_", " ").title()
        items.append(
            make_badge_text(analysis_decision_badge(decision), f" {phase_title}{reason_part}")
        )
    return items


def _wide_activity_section(
    snapshot: PipelineSnapshot,
    options: CompletionSummaryOptions,
    style: str,
) -> list[Rule | Text]:
    items: list[Rule | Text] = [Rule("Activity Summary", style=style)]
    if options.elapsed_seconds is not None:
        items.append(Text(f"  elapsed={format_elapsed_seconds(options.elapsed_seconds)}"))
    items.append(Text(f"  agent_calls={snapshot.total_agent_calls}"))
    items.append(Text(f"  content_blocks={options.content_block_count}"))
    items.append(Text(f"  thinking_blocks={options.thinking_block_count}"))
    items.append(Text(f"  tool_calls={options.tool_call_count}"))
    items.append(Text(f"  errors={options.error_count}"))
    if options.overflow_path is not None:
        items.append(Text(f"  raw_overflow={options.overflow_path}"))
    return items


def _wide_commit_section(
    workspace_root: Path | None,
    pipeline_policy: PipelinePolicy | None,
    pr_url: str | None,
) -> list[Rule | Text]:
    commit_lines = _commit_message_lines(workspace_root)
    if not commit_lines and not pr_url:
        return []
    commit_style = style_for_role("commit", pipeline_policy)
    items: list[Rule | Text] = [Rule("Commit", style=commit_style)]
    items.extend(Text(f"  {line}") for line in commit_lines)
    if pr_url:
        items.append(Text(f"  PR: {pr_url}"))
    return items


def _wide_tail_section(
    snapshot: PipelineSnapshot,
    pipeline_policy: PipelinePolicy | None,
    options: CompletionSummaryOptions,
    style: str,
) -> list[Rule | Text]:
    items: list[Rule | Text] = []
    if options.include_context_sections and snapshot.plan_risks:
        items.append(Rule("Open Risks", style=style_for_role("fix", pipeline_policy)))
        items.extend(Text(f"  - {risk}") for risk in snapshot.plan_risks)
    if snapshot.last_error:
        items.append(Rule("Error", style=style_for_terminal_failure(pipeline_policy)))
        items.append(Text(f"  {snapshot.last_error}"))
        diag = _children_persist_diagnostic_line(snapshot.last_error)
        if diag:
            items.append(Text(f"  {diag}"))
    dropped_line = _dropped_count_line(options.dropped_count)
    if dropped_line:
        items.append(Text(f"  {dropped_line}"))
    breadcrumb_lines = _debug_breadcrumb_lines(snapshot)
    if breadcrumb_lines:
        items.append(Rule("Debug", style="theme.text.muted"))
        items.extend(Text(f"  {line}") for line in breadcrumb_lines)
    return items


def render_completion_summary_group(
    snapshot: PipelineSnapshot,
    *,
    display_context: DisplayContext,
    options: CompletionSummaryOptions,
) -> Group:
    """Render the completion summary as a Rich Group with compact and wide layouts."""
    if display_context.mode == "compact":
        return _render_compact_group(snapshot, options=options)

    failed = snapshot.is_terminal_failure
    style = (
        style_for_terminal_failure(options.pipeline_policy)
        if failed
        else style_for_role("terminal", options.pipeline_policy)
    )
    title = "Pipeline Failed" if failed else "Pipeline Complete"

    renderables: list[Rule | Text] = [Rule(title, style=style)]
    renderables.append(Text(f"  exit={_exit_trigger_label(snapshot)}"))
    if options.elapsed_seconds is not None:
        renderables.append(Text(f"  elapsed={format_elapsed_seconds(options.elapsed_seconds)}"))

    renderables.extend(
        _wide_plan_section(snapshot, options.pipeline_policy, options.include_context_sections)
    )
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
    renderables.extend(_wide_decisions_section(snapshot, style))
    renderables.extend(_wide_review_section(snapshot, options.pipeline_policy))
    renderables.extend(_wide_analysis_section(snapshot, options.pipeline_policy, style))

    if _has_iteration_context(snapshot):
        renderables.append(Rule("Iteration Context", style=style))
        renderables.extend(Text(f"  {line}") for line in _iteration_context_lines(snapshot))

    renderables.extend(_wide_activity_section(snapshot, options, style))
    renderables.append(Rule("Verification", style=style))
    renderables.append(Text(f"  {_verification_line(options.workspace_root)}"))
    renderables.extend(
        _wide_commit_section(options.workspace_root, options.pipeline_policy, snapshot.pr_url)
    )
    renderables.extend(_wide_tail_section(snapshot, options.pipeline_policy, options, style))
    renderables.append(Rule(style=style))
    return Group(*renderables)


def _render_compact_group(
    snapshot: PipelineSnapshot,
    *,
    options: CompletionSummaryOptions,
) -> Group:
    failed = snapshot.is_terminal_failure
    style = (
        style_for_terminal_failure(options.pipeline_policy)
        if failed
        else style_for_role("terminal", options.pipeline_policy)
    )
    title = "Pipeline Failed" if failed else "Pipeline Complete"
    title_with_elapsed = (
        f"{title}  elapsed={format_elapsed_seconds(options.elapsed_seconds)}"
        if options.elapsed_seconds is not None
        else title
    )

    renderables: list[Text] = [Text(title_with_elapsed, style=style)]
    renderables.append(Text(f"EXIT: {_exit_trigger_label(snapshot)}"))

    if options.include_context_sections and (snapshot.plan_summary or snapshot.plan_scope_items):
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
    renderables.extend(_compact_decisions_items(snapshot))

    review_line = _review_summary_line(snapshot)
    if review_line is not None:
        badge, summary_text = review_line
        renderables.append(Text(f"REVIEW: [{badge}] {summary_text}".upper()))

    renderables.extend(_compact_analysis_items(snapshot))

    iter_lines = _iteration_context_lines(snapshot)
    if iter_lines:
        renderables.append(Text(f"CONTEXT: {' | '.join(iter_lines)}"))

    renderables.append(Text(f"VERIFICATION: {_verification_line(options.workspace_root)}"))

    activity_parts: list[str] = [
        f"agent_calls={snapshot.total_agent_calls}",
        f"content_blocks={options.content_block_count}",
        f"thinking_blocks={options.thinking_block_count}",
        f"tool_calls={options.tool_call_count}",
        f"errors={options.error_count}",
    ]
    if options.overflow_path is not None:
        activity_parts.append(f"raw_overflow={options.overflow_path}")
    renderables.append(Text("ACTIVITY: " + " ".join(activity_parts)))
    renderables.extend(
        _compact_tail_items(
            snapshot,
            options.workspace_root,
            options.dropped_count,
            options.include_context_sections,
        )
    )
    return Group(*renderables)
