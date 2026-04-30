"""Phase transition display for Ralph pipeline.

Renders visually distinct banners and separators at pipeline phase boundaries
so the user can easily follow the flow of planning → development → review → …
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.rule import Rule
from rich.text import Text

from ralph.display.context import make_display_context

if TYPE_CHECKING:
    from rich.console import Console

    from ralph.display.context import DisplayContext

_PHASE_STYLES: dict[str, str] = {
    "planning": "theme.phase.planning",
    "development": "theme.phase.development",
    "development_analysis": "theme.phase.development_analysis",
    "development_commit": "theme.phase.development_commit",
    "review": "theme.phase.review",
    "review_analysis": "theme.phase.review_analysis",
    "review_commit": "theme.phase.review_commit",
    "commit": "theme.phase.commit",
    "fix": "theme.phase.fix",
    "complete": "theme.phase.complete",
    "failed": "theme.phase.failed",
}

_MAJOR_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("planning", "development"),
        ("development_analysis", "development_commit"),
        ("development_analysis", "development"),
        ("development_commit", "review"),
        ("review", "complete"),
        ("review_analysis", "review_commit"),
        ("review_analysis", "fix"),
        ("fix", "review"),
        ("review_commit", "complete"),
        ("review_commit", "development"),
        ("review_commit", "planning"),
        ("development_commit", "planning"),
    }
)

_TRANSITION_DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("planning", "development"): "Plan ready — starting development",
    ("development", "development_analysis"): "Development complete — analyzing results",
    ("development_analysis", "development_commit"): "Analysis approved — committing changes",
    ("development_analysis", "development"): (
        "Analysis requested changes — returning to development"
    ),
    ("development_commit", "review"): "Changes committed — starting review",
    ("development_commit", "planning"): "Commit complete — re-planning needed",
    ("review", "review_analysis"): "Review complete — analyzing findings",
    ("review_analysis", "review_commit"): "Review analysis approved — committing review changes",
    ("review_analysis", "fix"): "Review found issues — routing to fix",
    ("fix", "review"): "Fix complete — re-reviewing",
    ("review_commit", "complete"): "Review changes committed — pipeline complete",
    ("review_commit", "development"): "Review committed — continuing development",
    ("review_commit", "planning"): "Review committed — re-planning needed",
    ("review", "complete"): "All reviews passed",
}


def _phase_style(phase: str) -> str:
    """Return the rich style string for a phase name."""
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


def _resolve_console(
    console: Console | None,
    display_context: DisplayContext | None,
) -> Console:
    """Resolve the console to use, preferring display_context.console when available."""
    if display_context is not None:
        return display_context.console
    return console or make_display_context().console


def show_phase_transition(
    from_phase: str,
    to_phase: str,
    *,
    context: dict[str, object] | None = None,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Display a visual transition between pipeline phases.

    Major transitions (e.g. planning → development) get a prominent banner.
    Minor transitions (e.g. development → development_analysis) get a simple rule.

    Args:
        from_phase: The phase being left.
        to_phase: The phase being entered.
        context: Optional key-value context to display alongside the transition.
        console: Rich console for output.
        display_context: DisplayContext whose console takes precedence over console.
    """
    c = _resolve_console(console, display_context)

    style = _phase_style(to_phase)
    from_label = _phase_label(from_phase)
    to_label = _phase_label(to_phase)
    description = _TRANSITION_DESCRIPTIONS.get((from_phase, to_phase))

    is_major = (from_phase, to_phase) in _MAJOR_TRANSITIONS
    mode = display_context.mode if display_context is not None else "wide"

    if is_major:
        if mode == "compact":
            slim_title = Text()
            slim_title.append(f"{from_label} → {to_label}", style=style)
            c.print(Rule(title=slim_title, style=style))
        else:
            if mode != "medium":
                c.print()
            c.print(Rule(style=style))
            banner = Text()
            banner.append(f"  {from_label}", style="theme.text.muted")
            banner.append(" → ", style="theme.text.emphasis")
            banner.append(to_label, style=style)
            if context:
                detail = "  ".join(f"{k}={v}" for k, v in context.items())
                banner.append(f"  ({detail})", style="theme.text.muted")
            c.print(banner)
            if description:
                c.print(Text(f"  {description}", style="theme.text.dim_italic"))
            c.print(Rule(style=style))
    else:
        if mode != "compact":
            c.print()
        title = Text()
        title.append(f"{from_label} → {to_label}")
        if description:
            title.append(f"  {description}", style="theme.text.dim_italic")
        c.print(Rule(title=title, style=style))


@dataclass(frozen=True)
class PhaseStartContext:
    """Optional counters and metadata for phase start display."""

    iteration: int | None = None
    total_iterations: int | None = None
    reviewer_pass: int | None = None
    total_reviewer_passes: int | None = None
    agent_name: str | None = None
    development_analysis_iteration: int | None = None
    max_development_analysis_iterations: int | None = None
    review_analysis_iteration: int | None = None
    max_review_analysis_iterations: int | None = None


def _build_analysis_suffix(
    iteration: int,
    max_iterations: int,
) -> str:
    """Build the analysis iteration suffix string."""
    return f" [analysis {iteration + 1}/{max_iterations}]"


def show_phase_start(
    phase: str,
    *,
    ctx: PhaseStartContext | None = None,
    agent_name: str | None = None,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Display the start of a pipeline phase.

    Args:
        phase: Phase name.
        ctx: Optional context with iteration/reviewer counters.
        agent_name: Name of the agent being invoked (shortcut; also settable via ctx).
        console: Rich console for output.
        display_context: DisplayContext whose console takes precedence over console.
    """
    c = _resolve_console(console, display_context)
    style = _phase_style(phase)
    label = _phase_label(phase)

    line = Text()
    line.append("▶ ", style=style)
    line.append(label, style=style)

    if ctx is not None:
        if ctx.iteration is not None and ctx.total_iterations is not None:
            line.append(
                f" [iteration {ctx.iteration + 1}/{ctx.total_iterations}]",
                style="theme.text.muted",
            )
        if ctx.reviewer_pass is not None and ctx.total_reviewer_passes is not None:
            line.append(
                f" [pass {ctx.reviewer_pass + 1}/{ctx.total_reviewer_passes}]",
                style="theme.text.muted",
            )
        if (
            phase == "development_analysis"
            and ctx.development_analysis_iteration is not None
            and ctx.max_development_analysis_iterations is not None
        ):
            suffix = _build_analysis_suffix(
                ctx.development_analysis_iteration,
                ctx.max_development_analysis_iterations,
            )
            line.append(suffix, style="theme.text.muted")
        if (
            phase == "review_analysis"
            and ctx.review_analysis_iteration is not None
            and ctx.max_review_analysis_iterations is not None
        ):
            suffix = _build_analysis_suffix(
                ctx.review_analysis_iteration,
                ctx.max_review_analysis_iterations,
            )
            line.append(suffix, style="theme.text.muted")
        effective_agent = ctx.agent_name or agent_name
    else:
        effective_agent = agent_name

    if effective_agent is not None:
        line.append(f"  agent={effective_agent}", style="theme.text.muted")

    c.print(line)


def _get_int_attr(obj: object, attr: str) -> int | None:
    """Extract a typed int attribute from any object, returning None if absent or wrong type."""
    val: object = getattr(obj, attr, None)
    return val if isinstance(val, int) else None


def _get_str_attr(obj: object, attr: str) -> str | None:
    """Extract a typed str attribute from any object, returning None if absent or wrong type."""
    val: object = getattr(obj, attr, None)
    return val if isinstance(val, str) else None


def show_phase_start_from_state(
    state: object,
    phase: str,
    *,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Display phase start using counters extracted from a pipeline state object.

    Args:
        state: Any object with optional iteration/reviewer/analysis counter attributes.
        phase: Phase name to display.
        console: Rich console for output.
        display_context: DisplayContext whose console takes precedence over console.
    """
    ctx = PhaseStartContext(
        iteration=_get_int_attr(state, "iteration"),
        total_iterations=_get_int_attr(state, "total_iterations"),
        reviewer_pass=_get_int_attr(state, "reviewer_pass"),
        total_reviewer_passes=_get_int_attr(state, "total_reviewer_passes"),
        agent_name=_get_str_attr(state, "agent_name"),
        development_analysis_iteration=_get_int_attr(state, "development_analysis_iteration"),
        max_development_analysis_iterations=_get_int_attr(
            state, "max_development_analysis_iterations"
        ),
        review_analysis_iteration=_get_int_attr(state, "review_analysis_iteration"),
        max_review_analysis_iterations=_get_int_attr(state, "max_review_analysis_iterations"),
    )
    show_phase_start(phase, ctx=ctx, console=console, display_context=display_context)


def show_phase_complete(
    phase: str,
    *,
    decision: str | None = None,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Display phase completion with an optional decision outcome.

    Args:
        phase: Phase that completed.
        decision: Optional decision (e.g. 'approved', 'needs changes').
        console: Rich console for output.
        display_context: DisplayContext whose console takes precedence over console.
    """
    c = _resolve_console(console, display_context)
    style = _phase_style(phase)
    label = _phase_label(phase)

    line = Text()
    line.append("✓ ", style=style)
    line.append(f"{label} complete", style=style)
    if decision is not None:
        line.append(f" — {decision}", style="theme.text.emphasis")

    c.print(line)
