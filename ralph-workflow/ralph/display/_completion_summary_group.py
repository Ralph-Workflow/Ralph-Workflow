"""Grouped Rich rendering helpers for completion summaries.

After the wt-028-display consolidation, the single ``default`` display
mode renders the full Rich layout (section rules, metrics, decisions,
review, analysis, iteration context, activity, verification, commit,
tail). The historical ``compact`` / ``wide`` split is collapsed into
this single rendering path.
"""

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


def _decisions_items(snapshot: PipelineSnapshot) -> list[Text]:
    if not snapshot.decision_log:
        return [Text("DECISIONS: (none recorded)")]
    items: list[Text] = []
    for phase, decision, reason, _ts in snapshot.decision_log:
        badge = _DECISION_LABELS.get(decision.lower(), "INFO")
        reason_part = f": {decision}" + (f" \u2014 {reason}" if reason else "")
        phase_title = phase.replace("_", " ").title()
        items.append(make_badge_text(badge, f" DECISIONS: {phase_title}{reason_part}"))
    return items


def _analysis_items(snapshot: PipelineSnapshot) -> list[Text]:
    items: list[Text] = []
    for phase, label, reason in _analysis_decision_summary(snapshot):
        badge = analysis_decision_badge(label)
        summary_text = f"{label}{f' — {reason}' if reason else ''}"
        items.append(
            make_badge_text(
                badge,
                f" ANALYSIS: {phase.replace('_', ' ').title()} {summary_text}",
            )
        )
    return items


def _tail_items(
    snapshot: PipelineSnapshot,
    workspace_root: Path | None,
    pipeline_policy: PipelinePolicy | None,
    dropped_count: int,
    include_context_sections: bool,
) -> list[Text | Rule]:
    items: list[Text | Rule] = []
    commit_lines = _commit_message_lines(workspace_root)
    if commit_lines:
        items.append(Text("COMMIT:"))
        items.extend(Text(f"  {line}") for line in commit_lines)
    if snapshot.is_terminal_failure:
        items.append(Rule("Error", style=style_for_terminal_failure(pipeline_policy)))
        items.append(Text(f"  {snapshot.last_error}"))
        last_error = snapshot.last_error if snapshot.last_error is not None else ""
        diag = _children_persist_diagnostic_line(last_error)
        if diag:
            items.append(Text(f"  {diag}"))
    dropped_line = _dropped_count_line(dropped_count)
    if dropped_line:
        items.append(Text(f"  {dropped_line}"))
    breadcrumb_lines = _debug_breadcrumb_lines(snapshot)
    if breadcrumb_lines:
        items.append(Rule("Debug", style="theme.text.muted"))
        items.extend(Text(f"  {line}") for line in breadcrumb_lines)
    return items


def _plan_section(
    snapshot: PipelineSnapshot,
    pipeline_policy: PipelinePolicy | None,
    include_context_sections: bool,
) -> list[Text | Rule]:
    items: list[Text | Rule] = []
    if include_context_sections and (snapshot.plan_summary or snapshot.plan_scope_items):
        items.append(Rule("Plan", style=style_for_role("terminal", pipeline_policy)))
        if snapshot.plan_summary:
            items.append(Text(f"  {snapshot.plan_summary}"))
        if snapshot.plan_scope_items:
            items.append(Text(f"  Scope: {len(snapshot.plan_scope_items)} item(s)"))
    return items


def _decisions_section(snapshot: PipelineSnapshot, style: str) -> list[Text | Rule]:
    items: list[Text | Rule] = [Rule("Decisions", style=style)]
    items.extend(_decisions_items(snapshot))
    return items


def _review_section(
    snapshot: PipelineSnapshot,
    pipeline_policy: PipelinePolicy | None,
) -> list[Text | Rule]:
    review_line = _review_summary_line(snapshot)
    if review_line is None:
        return []
    badge, summary_text = review_line
    return [
        Rule("Review", style=style_for_role("terminal", pipeline_policy)),
        Text(f"  [{badge}] {summary_text}".upper()),
    ]


def _analysis_section(
    snapshot: PipelineSnapshot,
    pipeline_policy: PipelinePolicy | None,
    style: str,
) -> list[Text | Rule]:
    items: list[Text | Rule] = [Rule("Analysis", style=style)]
    items.extend(_analysis_items(snapshot))
    return items


def _activity_section(
    snapshot: PipelineSnapshot,
    options: CompletionSummaryOptions,
    style: str,
) -> list[Text | Rule]:
    activity_parts: list[str] = [
        f"agent_calls={snapshot.total_agent_calls}",
        f"content_blocks={options.content_block_count}",
        f"thinking_blocks={options.thinking_block_count}",
        f"tool_calls={options.tool_call_count}",
        f"errors={options.error_count}",
    ]
    if options.overflow_path is not None:
        activity_parts.append(f"raw_overflow={options.overflow_path}")
    items: list[Text | Rule] = [
        Rule("Activity", style=style),
        Text("  " + " ".join(activity_parts)),
    ]
    return items


def _commit_section(
    workspace_root: Path | None,
    pipeline_policy: PipelinePolicy | None,
    pr_url: str | None,
) -> list[Text | Rule]:
    commit_lines = _commit_message_lines(workspace_root)
    if not commit_lines:
        return []
    items: list[Text | Rule] = [Rule("Commit", style=style_for_role("terminal", pipeline_policy))]
    items.extend(Text(f"  {line}") for line in commit_lines)
    if pr_url is not None:
        items.append(Text(f"  PR: {pr_url}"))
    return items


def render_completion_summary_group(
    snapshot: PipelineSnapshot,
    *,
    display_context: DisplayContext,
    options: CompletionSummaryOptions,
) -> Group:
    """Render the completion summary as a Rich Group (single default-mode layout)."""
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
        _plan_section(snapshot, options.pipeline_policy, options.include_context_sections)
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
    renderables.extend(_decisions_section(snapshot, style))
    renderables.extend(_review_section(snapshot, options.pipeline_policy))
    renderables.extend(_analysis_section(snapshot, options.pipeline_policy, style))

    if _has_iteration_context(snapshot):
        renderables.append(Rule("Iteration Context", style=style))
        renderables.extend(Text(f"  {line}") for line in _iteration_context_lines(snapshot))

    renderables.extend(_activity_section(snapshot, options, style))
    renderables.append(Rule("Verification", style=style))
    renderables.append(Text(f"  {_verification_line(options.workspace_root)}"))
    renderables.extend(
        _commit_section(options.workspace_root, options.pipeline_policy, snapshot.pr_url)
    )
    renderables.extend(
        _tail_items(
            snapshot,
            options.workspace_root,
            options.pipeline_policy,
            options.dropped_count,
            options.include_context_sections,
        )
    )
    renderables.append(Rule(style=style))
    return Group(*renderables)
