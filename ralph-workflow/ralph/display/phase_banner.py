"""Phase transition display for Ralph pipeline.

Renders visually distinct banners and separators at pipeline phase boundaries
so the user can easily follow the flow of planning → development → review → …
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.rule import Rule
from rich.text import Text

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.phase_status import (
    format_analysis_cycle,
    format_dev_cycle,
    format_elapsed_seconds,
    format_transition_context_items,
)

if TYPE_CHECKING:
    from rich.console import Console

    from ralph.display.phase_lifecycle import PhaseEntryModel, PhaseExitModel
    from ralph.policy.models import PipelinePolicy

_PHASE_STYLES: dict[str, str] = {
    "execution": "theme.phase.development",
    "analysis": "theme.phase.development_analysis",
    "review": "theme.phase.review",
    "commit": "theme.phase.commit",
    "fix": "theme.phase.fix",
    "verification": "theme.phase.development_analysis",
    "terminal": "theme.phase.complete",
    "fanout_join": "theme.phase.development",
}

# Role-pair based major transitions (used when pipeline_policy is available)
_MAJOR_ROLE_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("execution", "analysis"),
        ("analysis", "commit"),
        ("commit", "review"),
        ("review", "analysis"),
        ("analysis", "execution"),
        ("commit", "execution"),
        ("commit", "terminal"),
        ("review", "terminal"),
        ("execution", "terminal"),
    }
)


def _phase_style(phase: str, pipeline_policy: PipelinePolicy | None = None) -> str:
    """Return the rich style string for a phase name or role.

    When pipeline_policy is provided, the style is derived from the phase's
    declared role so renamed phases render with the correct color. Without a
    policy, the input is treated as a role key — canonical phase names are not
    recognized and return the muted default.
    """
    if pipeline_policy is not None:
        phase_def = pipeline_policy.phases.get(phase)
        if phase_def is not None:
            role = phase_def.role or ""
            terminal_outcome = phase_def.terminal_outcome
            if role == "terminal" and terminal_outcome == "failure":
                return "theme.phase.failed"
            style = _PHASE_STYLES.get(role)
            if style is not None:
                return style
    return _PHASE_STYLES.get(phase, "theme.text.muted")


def _phase_label(phase: str) -> str:
    """Return a human-readable label for a phase name.

    Examples:
        >>> _phase_label("development_analysis")
        'Development Analysis'
        >>> _phase_label("review_commit")
        'Review Commit'
    """
    return phase.replace("_", " ").title()


def _resolve_transition_meta(
    from_phase: str,
    to_phase: str,
    pipeline_policy: PipelinePolicy | None,
) -> bool:
    """Return is_major for a phase transition.

    Uses role-pair tables when policy is available. Without policy, the
    transition is treated as minor.
    """
    if pipeline_policy is None:
        return False
    phases = pipeline_policy.phases
    from_def = phases.get(from_phase)
    to_def = phases.get(to_phase)
    if from_def is None or to_def is None:
        return False
    from_role = from_def.role or ""
    to_role = to_def.role or ""
    return (from_role, to_role) in _MAJOR_ROLE_PAIRS


def _render_major_transition(  # noqa: PLR0913
    c: Console,
    from_label: str,
    to_label: str,
    style: str,
    context: dict[str, object] | None,
    arrow: str,
) -> None:
    """Render a major (prominent) phase transition banner."""
    title = Text()
    title.append(from_label, style="theme.text.muted")
    title.append(f" {arrow} ", style="theme.text.emphasis")
    title.append(to_label, style=style)
    if context:
        detail = "  ".join(format_transition_context_items(context))
        title.append(f"  ({detail})", style="theme.text.muted")
    c.print(Rule(title=title, style=style))


def _resolve_console(
    console: Console | None,
    display_context: DisplayContext | None,
) -> Console:
    if display_context is not None:
        return display_context.console
    if console is not None:
        return console
    raise TypeError("console or display_context is required")


def show_phase_transition(  # noqa: PLR0913
    from_phase: str,
    to_phase: str,
    *,
    context: dict[str, object] | None = None,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> None:
    """Display a visual transition between pipeline phases.

    Major transitions (e.g. planning → development) get a prominent banner.
    Minor transitions (e.g. development → development_analysis) get a simple rule.

    When pipeline_policy is provided, styles and descriptions are derived from
    declared phase roles so renamed phases render correctly.
    """
    c = _resolve_console(console, display_context)
    ctx = display_context if display_context is not None else make_display_context(console=c)

    style = _phase_style(to_phase, pipeline_policy)
    from_label = _phase_label(from_phase)
    to_label = _phase_label(to_phase)
    is_major = _resolve_transition_meta(from_phase, to_phase, pipeline_policy)

    if is_major:
        _render_major_transition(
            c,
            from_label,
            to_label,
            style,
            context,
            ctx.glyph_for("arrow"),
        )
        return

    if ctx.mode != "compact":
        c.print()
    title = Text()
    arrow = ctx.glyph_for("arrow")
    title.append(f"{from_label} {arrow} {to_label}")
    c.print(Rule(title=title, style=style))


def _build_outer_iteration_suffix(
    iteration: int | None,
    cap: int | None = None,
    *,
    od_glyph: str = "⊞",
    qualifier: str = "",
) -> str:
    """Build the outer dev cycle label string."""
    if iteration is None:
        return ""
    qual = f" {qualifier}" if qualifier else ""
    return f"  {od_glyph} {format_dev_cycle(iteration, cap)}{qual}"


def _build_inner_analysis_suffix(
    inner: int | None,
    max_inner: int | None = None,
    *,
    ia_glyph: str = "≴",
    qualifier: str = "",
) -> str:
    """Build the inner analysis cycle label string."""
    if inner is None:
        return ""
    qual = f" {qualifier}" if qualifier else ""
    return f"  {ia_glyph} {format_analysis_cycle(inner, max_inner)}{qual}"


def show_phase_start(
    phase: str,
    *,
    agent_name: str | None = None,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> None:
    """Display the start of a pipeline phase (no iteration context).

    For banners that carry iteration context, use :func:`show_phase_start_from_entry`.
    """
    c = _resolve_console(console, display_context)
    effective_ctx = (
        display_context if display_context is not None else make_display_context(console=c)
    )
    style = _phase_style(phase, pipeline_policy)
    label = _phase_label(phase)

    line = Text()
    start_glyph = effective_ctx.glyph_for("start")
    line.append(f"{start_glyph} ", style=style)
    line.append(label, style=style)

    if agent_name is not None:
        line.append(f"  agent={agent_name}", style="theme.text.muted")

    c.print(line)


def show_phase_start_from_entry(  # noqa: PLR0912
    entry: PhaseEntryModel,
    *,
    display_context: DisplayContext,
    pipeline_policy: PipelinePolicy | None = None,
) -> None:
    """Display the start of a pipeline phase from a lifecycle entry model.

    Canonical model-based path for phase-start banners.  Uses the entry model so
    iteration labels (Dev N/cap, Analysis N/cap) never diverge
    between phase-start and phase-close surfaces.

    Wide mode: a single titled Rule carries all context — start glyph, phase label,
    outer/inner qualifiers, and remaining-budget indicator — followed by an optional
    agent line.  No redundant banner line is emitted after the Rule.

    Medium mode: blank line + banner line with qualifiers and budget indicator.
    Compact mode: terse banner line, no qualifiers, no Rule.
    """
    c = display_context.console
    style = _phase_style(entry.phase_name, pipeline_policy)
    label = entry.human_label()
    mode = display_context.mode
    start_glyph = display_context.glyph_for("start")
    od_glyph = display_context.glyph_for("outer_dev")
    ia_glyph = display_context.glyph_for("inner_analysis")

    if mode == "wide":
        # All context goes into the Rule title — single source of truth for this phase section.
        # Qualifiers (outer)/(inner) appear here instead of on a separate redundant banner line.
        rule_title = Text()
        rule_title.append(f"{start_glyph} ", style=style)
        rule_title.append(label, style=style)
        if entry.outer_dev_iteration is not None:
            rule_title.append(
                _build_outer_iteration_suffix(
                    entry.outer_dev_iteration,
                    entry.outer_dev_cap,
                    od_glyph=od_glyph,
                    qualifier="(outer)",
                ),
                style="theme.outer_dev",
            )
        if entry.inner_analysis is not None:
            rule_title.append(
                _build_inner_analysis_suffix(
                    entry.inner_analysis,
                    entry.inner_analysis_cap,
                    ia_glyph=ia_glyph,
                    qualifier="(inner)",
                ),
                style="theme.inner_analysis",
            )
        if entry.inner_analysis is not None and entry.inner_analysis_cap is not None:
            remaining = entry.inner_analysis_cap - entry.inner_analysis
            if remaining > 0:
                rule_title.append(f"  [{remaining} left]", style="theme.text.muted")
            elif remaining == 0:
                rule_title.append("  [last]", style="theme.level.warn")
        c.print(Rule(title=rule_title, style=style))
        if entry.agent_name is not None:
            agent_line = Text()
            agent_line.append("    agent: ", style="theme.text.muted")
            agent_line.append(entry.agent_name, style="theme.text.emphasis")
            c.print(agent_line)
        return

    # Medium mode: blank line provides visual phase boundary without a full separator
    if mode == "medium":
        c.print()

    # Medium and compact mode: banner line with iteration context
    line = Text()
    line.append(f"{start_glyph} ", style=style)
    line.append(label, style=style)

    outer_qualifier = "(outer)" if mode == "medium" else ""
    inner_qualifier = "(inner)" if mode == "medium" else ""

    if entry.outer_dev_iteration is not None:
        suffix = _build_outer_iteration_suffix(
            entry.outer_dev_iteration,
            entry.outer_dev_cap,
            od_glyph=od_glyph,
            qualifier=outer_qualifier,
        )
        line.append(suffix, style="theme.outer_dev")

    if entry.inner_analysis is not None:
        suffix = _build_inner_analysis_suffix(
            entry.inner_analysis,
            entry.inner_analysis_cap,
            ia_glyph=ia_glyph,
            qualifier=inner_qualifier,
        )
        line.append(suffix, style="theme.inner_analysis")

    # Show remaining analysis slots in medium mode when cap is known
    if (
        mode == "medium"
        and entry.inner_analysis is not None
        and entry.inner_analysis_cap is not None
    ):
        remaining = entry.inner_analysis_cap - entry.inner_analysis
        if remaining > 0:
            line.append(f"  [{remaining} left]", style="theme.text.muted")
        elif remaining == 0:
            line.append("  [last]", style="theme.level.warn")

    if entry.agent_name is not None:
        line.append(f"  agent={entry.agent_name}", style="theme.text.muted")

    c.print(line)


def _build_phase_close_stats_line(
    exit_model: PhaseExitModel,
    display_context: DisplayContext,
) -> Text | None:
    """Build an activity-stats supplementary line for the phase-close banner.

    Returns None when all counters are zero or when in compact mode.
    In medium and wide mode surfaces content/thinking/tool/error counts so
    the phase-close banner gives a full picture of agent activity.
    """
    if display_context.mode == "compact":
        return None
    total = (
        exit_model.content_blocks
        + exit_model.thinking_blocks
        + exit_model.tool_calls
        + exit_model.errors
    )
    if total == 0:
        return None
    stats = Text()
    stats.append("    ↳ stats: ", style="theme.text.muted")
    parts: list[tuple[str, str]] = [
        (f"content={exit_model.content_blocks}", "theme.text.muted"),
        (f"thinking={exit_model.thinking_blocks}", "theme.text.muted"),
        (f"tools={exit_model.tool_calls}", "theme.text.muted"),
    ]
    if exit_model.errors > 0:
        parts.append((f"errors={exit_model.errors}", "theme.level.error"))
    for i, (part_text, part_style) in enumerate(parts):
        if i > 0:
            stats.append("  ", style="theme.text.muted")
        stats.append(part_text, style=part_style)
    return stats


def _build_review_outcome_line(
    exit_model: PhaseExitModel,
    display_context: DisplayContext,
) -> Text | None:
    """Build a review outcome line if review_issues_found is set.

    Returns None when review_issues_found is None (not applicable).
    Review outcome is always shown regardless of display mode since it is
    critical UX information about whether review passed or found issues.
    """
    if exit_model.review_issues_found is None:
        return None
    review_line = Text()
    review_glyph_pass = display_context.glyph_for("review_pass")
    review_glyph_fail = display_context.glyph_for("review_fail")
    if exit_model.review_issues_found:
        review_line.append(f"    {review_glyph_fail} ", style="theme.review_fail")
        review_line.append("review: ", style="theme.text.muted")
        review_line.append("issues found", style="theme.level.error")
    else:
        review_line.append(f"    {review_glyph_pass} ", style="theme.review_pass")
        review_line.append("review: ", style="theme.text.muted")
        review_line.append("clean", style="theme.status.success")
    return review_line


def _build_debug_line(
    exit_model: PhaseExitModel,
    display_context: DisplayContext,
) -> Text | None:
    """Build a debug breadcrumb line if waiting status or failure category is set.

    Returns None when neither is set.
    """
    if not exit_model.waiting_status_line and not exit_model.last_failure_category:
        return None
    debug_line = Text()
    warning_glyph = display_context.glyph_for("warning")
    debug_parts: list[str] = []
    if exit_model.waiting_status_line:
        debug_parts.append(f"waiting: {exit_model.waiting_status_line[:80]}")
    if exit_model.last_failure_category:
        debug_parts.append(f"failure: {exit_model.last_failure_category}")
    debug_line.append(f"  {warning_glyph} debug: ", style="theme.level.warn")
    debug_line.append(" | ".join(debug_parts), style="theme.text.muted")
    return debug_line


def _print_wide_close_rule(
    style: str,
    console: Console,
    *,
    elapsed_seconds: float = 0.0,
    exit_trigger: str | None = None,
    arrow: str = "→",
) -> None:
    """Print the wide-mode trailing titled Rule as the section-close separator.

    When elapsed time and/or exit trigger are available, they form the Rule title
    so the section footer mirrors the header and is immediately readable when
    scrolling through output. Falls back to a plain Rule when both are absent.
    """
    parts: list[str] = []
    if elapsed_seconds > 0:
        parts.append(format_elapsed_seconds(elapsed_seconds))
    if exit_trigger is not None:
        parts.append(f"{arrow} {exit_trigger}")
    if parts:
        console.print(Rule(title="  ".join(parts), style=style))
    else:
        console.print(Rule(style=style))


def show_phase_close_banner(
    exit_model: PhaseExitModel,
    *,
    display_context: DisplayContext,
    pipeline_policy: PipelinePolicy | None = None,
) -> None:
    """Display the close of a pipeline phase from a lifecycle exit model.

    Canonical model-based path for phase-close rich banners. Symmetric with
    :func:`show_phase_start_from_entry`: same field ordering, same glyphs, same
    style keys. Appends elapsed time and exit trigger after the iteration context.

    In medium and wide modes an additional stats line surfaces content/thinking/
    tool/error counters from the exit model so the close banner is a full
    phase-level performance report.
    """
    c = display_context.console
    style = _phase_style(exit_model.phase_name, pipeline_policy)
    label = _phase_label(exit_model.phase_name)

    line = Text()
    success_glyph = display_context.glyph_for("success")
    od_glyph = display_context.glyph_for("outer_dev")
    ia_glyph = display_context.glyph_for("inner_analysis")
    arrow = display_context.glyph_for("arrow")
    line.append(f"{success_glyph} ", style=style)
    line.append(label, style=style)

    mode = display_context.mode
    outer_qualifier = "(outer)" if mode in ("medium", "wide") else ""
    inner_qualifier = "(inner)" if mode in ("medium", "wide") else ""

    if exit_model.outer_dev_iteration is not None:
        suffix = _build_outer_iteration_suffix(
            exit_model.outer_dev_iteration,
            exit_model.outer_dev_cap,
            od_glyph=od_glyph,
            qualifier=outer_qualifier,
        )
        line.append(suffix, style="theme.outer_dev")

    if exit_model.inner_analysis is not None:
        suffix = _build_inner_analysis_suffix(
            exit_model.inner_analysis,
            exit_model.inner_analysis_cap,
            ia_glyph=ia_glyph,
            qualifier=inner_qualifier,
        )
        line.append(suffix, style="theme.inner_analysis")

    if exit_model.elapsed_seconds > 0:
        line.append(
            f"  {format_elapsed_seconds(exit_model.elapsed_seconds)}",
            style="theme.text.muted",
        )

    if exit_model.exit_trigger is not None:
        line.append(f"  {arrow} {exit_model.exit_trigger}", style="theme.text.muted")

    c.print(line)

    stats_line = _build_phase_close_stats_line(exit_model, display_context)
    if stats_line is not None:
        c.print(stats_line)

    if exit_model.artifact_outcome and mode != "compact":
        artifact_line = Text()
        artifact_line.append("    ↳ artifact: ", style="theme.text.muted")
        artifact_line.append(exit_model.artifact_outcome, style="theme.text.emphasis")
        c.print(artifact_line)

    review_line = _build_review_outcome_line(exit_model, display_context)
    if review_line is not None:
        c.print(review_line)

    # Routing note — explains why an adjacent phase was skipped (e.g. analysis cap reached).
    # Shown in all modes since it is actionable routing context, not merely decorative.
    if exit_model.routing_note is not None:
        routing_line = Text()
        routing_line.append(f"  {arrow} ", style="theme.text.muted")
        routing_line.append(exit_model.routing_note, style="theme.level.warn")
        c.print(routing_line)

    debug_line = _build_debug_line(exit_model, display_context)
    if debug_line is not None:
        c.print(debug_line)

    # Wide mode: titled trailing Rule closes the section visually.
    # The title mirrors the header so the section footer is readable when scrolling.
    if mode == "wide":
        _print_wide_close_rule(
            style,
            c,
            elapsed_seconds=exit_model.elapsed_seconds,
            exit_trigger=exit_model.exit_trigger,
            arrow=arrow,
        )
