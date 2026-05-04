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
    format_budget_remaining,
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

# Role-pair based transition descriptions
_ROLE_PAIR_DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("execution", "analysis"): "Work complete — analyzing results",
    ("analysis", "commit"): "Analysis approved — committing changes",
    ("analysis", "execution"): "Analysis requested changes — returning to work",
    ("commit", "review"): "Changes committed — starting review",
    ("commit", "execution"): "Commit complete — continuing work",
    ("review", "analysis"): "Review complete — analyzing findings",
    ("analysis", "review"): "Analysis approved — reviewing changes",
    ("commit", "terminal"): "Commit complete — pipeline finished",
    ("review", "terminal"): "Review complete — pipeline finished",
    ("execution", "terminal"): "Work complete — pipeline finished",
}


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
) -> tuple[str | None, bool]:
    """Return (description, is_major) for a phase transition.

    Uses role-pair tables when policy is available. Without policy, no
    description is shown and the transition is treated as minor.
    """
    if pipeline_policy is None:
        return None, False
    phases = pipeline_policy.phases
    from_def = phases.get(from_phase)
    to_def = phases.get(to_phase)
    if from_def is None or to_def is None:
        return None, False
    from_role = from_def.role or ""
    to_role = to_def.role or ""
    description = _ROLE_PAIR_DESCRIPTIONS.get((from_role, to_role))
    is_major = (from_role, to_role) in _MAJOR_ROLE_PAIRS
    return description, is_major


def _render_major_transition(  # noqa: PLR0913
    c: Console,
    from_label: str,
    to_label: str,
    style: str,
    description: str | None,
    context: dict[str, object] | None,
    mode: str,
    arrow: str,
) -> None:
    """Render a major (prominent) phase transition banner."""
    if mode == "compact":
        slim_title = Text()
        slim_title.append(f"{from_label} → {to_label}", style=style)
        c.print(Rule(title=slim_title, style=style))
        return
    if mode != "medium":
        c.print()
    c.print(Rule(style=style))
    banner = Text()
    banner.append(f"  {from_label}", style="theme.text.muted")
    banner.append(f" {arrow} ", style="theme.text.emphasis")
    banner.append(to_label, style=style)
    if context:
        detail = "  ".join(format_transition_context_items(context))
        banner.append(f"  ({detail})", style="theme.text.muted")
    c.print(banner)
    if description:
        c.print(Text(f"  {description}", style="theme.text.dim_italic"))
    c.print(Rule(style=style))


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
    description, is_major = _resolve_transition_meta(from_phase, to_phase, pipeline_policy)

    if is_major:
        _render_major_transition(
            c,
            from_label,
            to_label,
            style,
            description,
            context,
            ctx.mode,
            ctx.glyph_for("arrow"),
        )
        return

    if ctx.mode != "compact":
        c.print()
    title = Text()
    arrow = ctx.glyph_for("arrow")
    title.append(f"{from_label} {arrow} {to_label}")
    if description:
        title.append(f"  {description}", style="theme.text.dim_italic")
    c.print(Rule(title=title, style=style))


def _build_outer_iteration_suffix(
    iteration: int | None,
    cap: int | None = None,
    *,
    od_glyph: str = "⊞",
) -> str:
    """Build the outer dev cycle label string."""
    if iteration is None:
        return ""
    return f"  {od_glyph} {format_dev_cycle(iteration, cap)}"


def _build_inner_analysis_suffix(
    inner: int | None,
    max_inner: int | None = None,
    *,
    ia_glyph: str = "≴",
) -> str:
    """Build the inner analysis cycle label string."""
    if inner is None:
        return ""
    return f"  {ia_glyph} {format_analysis_cycle(inner, max_inner)}"


def _build_budget_remaining_suffix(
    remaining: int | None,
    *,
    budget_glyph: str = "▲",
) -> str:
    """Build the budget remaining label string."""
    if remaining is None:
        return ""
    return f"  {budget_glyph} {format_budget_remaining(remaining)}"


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


def show_phase_start_from_entry(
    entry: PhaseEntryModel,
    *,
    display_context: DisplayContext,
    pipeline_policy: PipelinePolicy | None = None,
) -> None:
    """Display the start of a pipeline phase from a lifecycle entry model.

    Canonical model-based path for phase-start banners.  Uses the entry model so
    iteration labels (Dev N/cap, Analysis N/cap, Budget: N left) never diverge
    between phase-start and phase-close surfaces.
    """
    c = display_context.console
    style = _phase_style(entry.phase_name, pipeline_policy)
    label = entry.human_label()

    line = Text()
    start_glyph = display_context.glyph_for("start")
    od_glyph = display_context.glyph_for("outer_dev")
    ia_glyph = display_context.glyph_for("inner_analysis")
    budget_glyph = display_context.glyph_for("budget")
    line.append(f"{start_glyph} ", style=style)
    line.append(label, style=style)

    if entry.outer_dev_iteration is not None:
        suffix = _build_outer_iteration_suffix(
            entry.outer_dev_iteration, entry.outer_dev_cap, od_glyph=od_glyph
        )
        line.append(suffix, style="theme.outer_dev")

    if entry.inner_analysis is not None:
        suffix = _build_inner_analysis_suffix(
            entry.inner_analysis, entry.inner_analysis_cap, ia_glyph=ia_glyph
        )
        line.append(suffix, style="theme.inner_analysis")

    if entry.budget_remaining is not None:
        suffix = _build_budget_remaining_suffix(entry.budget_remaining, budget_glyph=budget_glyph)
        line.append(suffix, style="theme.level.warn")

    if entry.agent_name is not None:
        line.append(f"  agent={entry.agent_name}", style="theme.text.muted")

    c.print(line)


def show_phase_complete(
    phase: str,
    *,
    decision: str | None = None,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> None:
    """Display phase completion with an optional decision outcome."""
    c = _resolve_console(console, display_context)
    effective_ctx = (
        display_context if display_context is not None else make_display_context(console=c)
    )
    style = _phase_style(phase, pipeline_policy)
    label = _phase_label(phase)

    line = Text()
    success_glyph = effective_ctx.glyph_for("success")
    line.append(f"{success_glyph} ", style=style)
    line.append(f"{label} complete", style=style)
    if decision is not None:
        line.append(f" — {decision}", style="theme.text.emphasis")

    c.print(line)


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
    """
    c = display_context.console
    style = _phase_style(exit_model.phase_name, pipeline_policy)
    label = _phase_label(exit_model.phase_name)

    line = Text()
    success_glyph = display_context.glyph_for("success")
    od_glyph = display_context.glyph_for("outer_dev")
    ia_glyph = display_context.glyph_for("inner_analysis")
    budget_glyph = display_context.glyph_for("budget")
    arrow = display_context.glyph_for("arrow")
    line.append(f"{success_glyph} ", style=style)
    line.append(label, style=style)

    if exit_model.outer_dev_iteration is not None:
        suffix = _build_outer_iteration_suffix(
            exit_model.outer_dev_iteration, exit_model.outer_dev_cap, od_glyph=od_glyph
        )
        line.append(suffix, style="theme.outer_dev")

    if exit_model.inner_analysis is not None:
        suffix = _build_inner_analysis_suffix(
            exit_model.inner_analysis, exit_model.inner_analysis_cap, ia_glyph=ia_glyph
        )
        line.append(suffix, style="theme.inner_analysis")

    if exit_model.budget_remaining is not None:
        suffix = _build_budget_remaining_suffix(
            exit_model.budget_remaining, budget_glyph=budget_glyph
        )
        line.append(suffix, style="theme.level.warn")

    if exit_model.elapsed_seconds > 0:
        elapsed_label = format_elapsed_seconds(exit_model.elapsed_seconds)
        line.append(f"  {elapsed_label}", style="theme.text.muted")

    if exit_model.exit_trigger is not None:
        line.append(f"  {arrow} {exit_model.exit_trigger}", style="theme.text.muted")

    c.print(line)
