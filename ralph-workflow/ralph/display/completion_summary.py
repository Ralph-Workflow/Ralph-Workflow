"""End-of-run completion summary rendering for log-first output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, cast

from rich.text import Text

from ralph.display._decision_labels import (
    DECISION_BADGE_MAP as _DECISION_LABELS,
)
from ralph.display._decision_labels import (
    PROCEED_DECISION_VALUES as _PROCEED_DECISIONS,
)
from ralph.display._decision_labels import (
    REVISE_DECISION_VALUES as _REVISE_DECISIONS,
)
from ralph.display.phase_banner import phase_style
from ralph.display.phase_status import (
    format_dev_cycle,
    format_elapsed_seconds,
    format_exit_trigger,
)
from ralph.mcp.artifacts.commit_message import read_commit_message_artifact

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Protocol

    from rich.console import Group

    from ralph.display.context import DisplayContext
    from ralph.display.snapshot import PipelineSnapshot
    from ralph.policy.models import PipelinePolicy

    class _RenderCompletionSummaryGroupFn(Protocol):
        def __call__(
            self,
            snapshot: PipelineSnapshot,
            *,
            display_context: DisplayContext,
            options: CompletionSummaryOptions,
        ) -> Group: ...


_VERIFICATION_ARTIFACT = ".agent/artifacts/verification.json"

_BADGE_THEME_KEYS: dict[str, str] = {
    "PASS": "theme.status.success",
    "INFO": "theme.level.info",
    "WARN": "theme.level.warn",
    "FAIL": "theme.status.failure",
}

_CHILDREN_PERSIST_MARKER = "kept child agents alive"
_KV_PATTERN = re.compile(r"(\w+)=([^,)\s]+)")


@dataclass(frozen=True)
class CompletionSummaryOptions:
    """Optional statistics and formatting parameters for completion summary rendering."""

    workspace_root: Path | None = None
    dropped_count: int = 0
    content_block_count: int = 0
    thinking_block_count: int = 0
    tool_call_count: int = 0
    error_count: int = 0
    elapsed_seconds: float | None = None
    overflow_path: str | None = None
    include_context_sections: bool = True
    pipeline_policy: PipelinePolicy | None = None


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
    lines = []
    if snapshot.last_activity_line:
        lines.append(f"last_activity: {snapshot.last_activity_line}")
    if snapshot.waiting_status_line:
        lines.append(f"waiting: {snapshot.waiting_status_line}")
    if snapshot.last_failure_category:
        lines.append(f"failure_category: {snapshot.last_failure_category}")
    if snapshot.mcp_restart_count > 0:
        lines.append(f"mcp_restarts: {snapshot.mcp_restart_count}")
    if snapshot.active_process_labels:
        lines.append(f"active_processes: {', '.join(snapshot.active_process_labels)}")
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
            if normalized in _PROCEED_DECISIONS:
                label = "proceed"
            elif normalized in _REVISE_DECISIONS:
                label = "revise"
            else:
                label = decision
            results.append((phase, label, reason))
    return results


def analysis_decision_badge(decision: str) -> str:
    """Canonical badge for a normalized analysis decision (single source of truth).

    ``decision`` is the normalized label from :func:`_analysis_decision_summary`
    ("proceed"/"revise"/other).
    """
    if decision == "proceed":
        return "PASS"
    if decision == "revise":
        return "WARN"
    return "INFO"


def _exit_trigger_label(snapshot: PipelineSnapshot) -> str:
    """Return a human-readable exit trigger label derived from snapshot state."""
    return format_exit_trigger(snapshot)


def _has_iteration_context(snapshot: PipelineSnapshot) -> bool:
    """Return True when any iteration context field is populated."""
    return snapshot.outer_dev_iteration is not None


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


def style_for_role(
    role: str,
    pipeline_policy: PipelinePolicy | None,
) -> str:
    """Return the style for the first phase with the given role, or muted when none matches."""
    if pipeline_policy is not None:
        for phase_name, phase_def in pipeline_policy.phases.items():
            if phase_def.role == role:
                return phase_style(phase_name, pipeline_policy)
    return "theme.text.muted"


def style_for_terminal_failure(
    pipeline_policy: PipelinePolicy | None,
) -> str:
    """Return the style for the terminal failure phase, or the failed theme default."""
    if pipeline_policy is not None:
        for phase_name, phase_def in pipeline_policy.phases.items():
            if phase_def.role == "terminal" and phase_def.terminal_outcome == "failure":
                return phase_style(phase_name, pipeline_policy)
    return "theme.phase.failed"


def _plain_decision_lines(snapshot: PipelineSnapshot) -> list[str]:
    if not snapshot.decision_log:
        return ["Decisions: (none recorded)"]
    lines = ["Decisions:"]
    for phase, decision, reason, _ts in snapshot.decision_log:
        badge = _DECISION_LABELS.get(decision.lower(), "INFO")
        reason_part = f" — {reason}" if reason else ""
        lines.append(f"- [{badge}] {phase.replace('_', ' ').title()}: {decision}{reason_part}")
    return lines


def _plain_analysis_lines(snapshot: PipelineSnapshot) -> list[str]:
    analysis_decisions = _analysis_decision_summary(snapshot)
    if not analysis_decisions:
        return []
    lines = ["Analysis Decisions:"]
    for phase, decision, reason in analysis_decisions:
        reason_part = f" — {reason}" if reason else ""
        lines.append(f"- {phase.replace('_', ' ').title()}: {decision}{reason_part}")
    return lines


def _plain_tail_lines(
    snapshot: PipelineSnapshot,
    workspace_root: Path | None,
    dropped_count: int,
) -> list[str]:
    lines: list[str] = []
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
    return lines


def render_completion_summary(
    snapshot: PipelineSnapshot,
    *,
    options: CompletionSummaryOptions | None = None,
) -> Text:
    """Build a rich ``Text`` object summarising pipeline completion for the terminal."""
    opts = options or CompletionSummaryOptions()
    failed = snapshot.is_terminal_failure
    lines: list[str] = ["Pipeline Failed" if failed else "Pipeline Complete"]

    lines.append(f"Exit: {_exit_trigger_label(snapshot)}")

    if opts.elapsed_seconds is not None:
        lines.append(f"Elapsed: {format_elapsed_seconds(opts.elapsed_seconds)}")

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
    if opts.elapsed_seconds is not None:
        activity_parts.append(f"elapsed={format_elapsed_seconds(opts.elapsed_seconds)}")
    activity_parts.append(f"content_blocks={opts.content_block_count}")
    activity_parts.append(f"thinking_blocks={opts.thinking_block_count}")
    activity_parts.append(f"tool_calls={opts.tool_call_count}")
    activity_parts.append(f"errors={opts.error_count}")
    lines.append("Activity: " + " ".join(activity_parts))

    lines.extend(_plain_decision_lines(snapshot))

    review_line = _review_summary_line(snapshot)
    if review_line is not None:
        badge, summary_text = review_line
        lines.append(f"Review: [{badge}] {summary_text}")

    lines.extend(_plain_analysis_lines(snapshot))

    iter_lines = _iteration_context_lines(snapshot)
    if iter_lines:
        lines.append("Iteration Context:")
        lines.extend(f"  {ln}" for ln in iter_lines)

    lines.extend(_plain_tail_lines(snapshot, opts.workspace_root, opts.dropped_count))

    return Text("\n".join(lines))


def make_badge_text(badge: str, rest: str) -> Text:
    """Build a Text object with a themed badge label followed by muted rest text."""
    theme_key = _BADGE_THEME_KEYS.get(badge, "theme.level.info")
    text = Text("  ")
    text.append(f"[{badge}]", style=theme_key)
    text.append(rest, style="theme.text.muted")
    return text


def render_completion_summary_group(
    snapshot: PipelineSnapshot,
    *,
    display_context: DisplayContext,
    options: CompletionSummaryOptions | None = None,
) -> Group:
    """Render the completion summary as a Rich Group with rule-delimited sections."""
    render_fn = cast(
        "_RenderCompletionSummaryGroupFn",
        import_module("ralph.display._completion_summary_group").render_completion_summary_group,
    )

    return render_fn(
        snapshot,
        display_context=display_context,
        options=options or CompletionSummaryOptions(),
    )


def emit_completion_summary(
    snapshot: PipelineSnapshot,
    *,
    display_context: DisplayContext,
    options: CompletionSummaryOptions | None = None,
) -> None:
    """Emit the completion summary to the console.

    Args:
        snapshot: Pipeline snapshot with run metadata.
        display_context: DisplayContext providing the console and mode.
        options: Optional statistics and formatting parameters.
    """
    display_context.console.print(
        render_completion_summary_group(snapshot, display_context=display_context, options=options),
        markup=False,
        highlight=False,
    )


__all__ = [
    "CompletionSummaryOptions",
    "emit_completion_summary",
    "make_badge_text",
    "render_completion_summary",
    "render_completion_summary_group",
    "style_for_role",
    "style_for_terminal_failure",
]
