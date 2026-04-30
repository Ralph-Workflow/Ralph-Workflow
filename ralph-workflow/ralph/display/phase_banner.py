"""Phase transition display for Ralph pipeline.

Renders visually distinct banners and separators at pipeline phase boundaries
so the user can easily follow the flow of planning \u2192 development \u2192 review \u2192 \u2026
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from rich.rule import Rule
from rich.text import Text

if TYPE_CHECKING:
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
    ("planning", "development"): "Plan ready \u2014 starting development",
    ("development", "development_analysis"): "Development complete \u2014 analyzing results",
    ("development_analysis", "development_commit"): "Analysis approved \u2014 committing changes",
    ("development_analysis", "development"): (
        "Analysis requested changes \u2014 returning to development"
    ),
    ("development_commit", "review"): "Changes committed \u2014 starting review",
    ("development_commit", "planning"): "Commit complete \u2014 re-planning needed",
    ("review", "review_analysis"): "Review complete \u2014 analyzing findings",
    (
        "review_analysis",
        "review_commit",
    ): "Review analysis approved \u2014 committing review changes",
    ("review_analysis", "fix"): "Review found issues \u2014 routing to fix",
    ("fix", "review"): "Fix complete \u2014 re-reviewing",
    ("review_commit", "complete"): "Review changes committed \u2014 pipeline complete",
    ("review_commit", "development"): "Review committed \u2014 continuing development",
    ("review_commit", "planning"): "Review committed \u2014 re-planning needed",
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


@dataclass(frozen=True)
class _TransitionLayout:
    """Layout knobs for phase transition banner rendering."""

    leading_blank: bool
    separator_rule: bool
    trailing_rule: bool


@dataclass(frozen=True)
class _BannerOptions:
    """Options for rendering a transition banner."""

    from_label: str
    to_label: str
    description: str | None = None
    context: dict[str, object] | None = None
    style: str = "theme.text.muted"


_MODE_LAYOUTS: dict[Literal["compact", "medium", "wide"], _TransitionLayout] = {
    "compact": _TransitionLayout(leading_blank=False, separator_rule=False, trailing_rule=True),
    "medium": _TransitionLayout(leading_blank=True, separator_rule=True, trailing_rule=True),
    "wide": _TransitionLayout(leading_blank=True, separator_rule=True, trailing_rule=True),
}


def _render_transition_banner(
    display_context: DisplayContext,
    options: _BannerOptions,
    is_major: bool,
) -> None:
    """Render a phase transition banner using mode-driven layout.

    Args:
        display_context: DisplayContext providing console and mode.
        options: Banner options including labels, description, context, and style.
        is_major: True for major transitions, False for minor.
    """
    c = display_context.console
    mode = display_context.mode
    layout = _MODE_LAYOUTS[mode]

    if layout.leading_blank:
        c.print()

    if layout.separator_rule:
        c.print(Rule(style=options.style))

    banner = Text()
    arrow = display_context.glyph_for("arrow")
    banner.append(f"  {options.from_label}", style="theme.text.muted")
    banner.append(f" {arrow} ", style="theme.text.emphasis")
    banner.append(options.to_label, style=options.style)
    if options.context:
        detail = "  ".join(f"{k}={v}" for k, v in options.context.items())
        banner.append(f"  ({detail})", style="theme.text.muted")
    c.print(banner)

    if options.description and is_major:
        c.print(Text(f"  {options.description}", style="theme.text.dim_italic"))

    if layout.trailing_rule:
        c.print(Rule(style=options.style))


def show_phase_transition(
    from_phase: str,
    to_phase: str,
    *,
    context: dict[str, object] | None = None,
    display_context: DisplayContext,
) -> None:
    """Display a visual transition between pipeline phases.

    Major transitions (e.g. planning \u2192 development) get a prominent banner.
    Minor transitions (e.g. development \u2192 development_analysis) get a simple rule.

    Args:
        from_phase: The phase being left.
        to_phase: The phase being entered.
        context: Optional key-value context to display alongside the transition.
        display_context: DisplayContext providing console and mode.
    """
    style = _phase_style(to_phase)
    from_label = _phase_label(from_phase)
    to_label = _phase_label(to_phase)
    description = _TRANSITION_DESCRIPTIONS.get((from_phase, to_phase))

    is_major = (from_phase, to_phase) in _MAJOR_TRANSITIONS

    if is_major:
        banner_options = _BannerOptions(
            from_label=from_label,
            to_label=to_label,
            description=description,
            context=context,
            style=style,
        )
        _render_transition_banner(
            display_context,
            banner_options,
            is_major=True,
        )
    else:
        # Minor transition: simple rule with title
        if display_context.mode != "compact":
            display_context.console.print()
        title = Text()
        arrow = display_context.glyph_for("arrow")
        title.append(f"{from_label} {arrow} {to_label}")
        if description:
            title.append(f"  {description}", style="theme.text.dim_italic")
        display_context.console.print(Rule(title=title, style=style))


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
    display_context: DisplayContext,
) -> None:
    """Display the start of a pipeline phase.

    Args:
        phase: Phase name.
        ctx: Optional context with iteration/reviewer counters.
        agent_name: Name of the agent being invoked (shortcut; also settable via ctx).
        display_context: DisplayContext providing the console for output.
    """
    c = display_context.console
    style = _phase_style(phase)
    label = _phase_label(phase)

    line = Text()
    start_glyph = display_context.glyph_for("start")
    line.append(f"{start_glyph} ", style=style)
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
    display_context: DisplayContext,
) -> None:
    """Display phase start using counters extracted from a pipeline state object.

    Args:
        state: Any object with optional iteration/reviewer/analysis counter attributes.
        phase: Phase name to display.
        display_context: DisplayContext providing the console for output.
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
    show_phase_start(phase, ctx=ctx, display_context=display_context)


def show_phase_complete(
    phase: str,
    *,
    decision: str | None = None,
    display_context: DisplayContext,
) -> None:
    """Display phase completion with an optional decision outcome.

    Args:
        phase: Phase that completed.
        decision: Optional decision (e.g. 'approved', 'needs changes').
        display_context: DisplayContext providing the console for output.
    """
    c = display_context.console
    style = _phase_style(phase)
    label = _phase_label(phase)

    line = Text()
    success_glyph = display_context.glyph_for("success")
    line.append(f"{success_glyph} ", style=style)
    line.append(f"{label} complete", style=style)
    if decision is not None:
        line.append(f" \u2014 {decision}", style="theme.text.emphasis")

    c.print(line)
