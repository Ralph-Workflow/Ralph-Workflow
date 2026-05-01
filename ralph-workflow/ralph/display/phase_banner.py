"""Phase transition display for Ralph pipeline.

Renders visually distinct banners and separators at pipeline phase boundaries
so the user can easily follow the flow of planning → development → review → …
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from rich.rule import Rule
from rich.text import Text

from ralph.display.context import DisplayContext, make_display_context

if TYPE_CHECKING:
    from rich.console import Console

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
        detail = "  ".join(f"{k}={v}" for k, v in context.items())
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


@dataclass(frozen=True)
class PhaseStartContext:
    """Optional counters and metadata for phase start display."""

    budget_progress: dict[str, tuple[int, int]] = field(default_factory=dict)
    agent_name: str | None = None
    analysis_iteration: int | None = None
    max_analysis_iterations: int | None = None


def _build_analysis_suffix(
    iteration: int,
    max_iterations: int,
) -> str:
    """Build the analysis iteration suffix string."""
    return f" [analysis {iteration + 1}/{max_iterations}]"


def show_phase_start(  # noqa: PLR0913
    phase: str,
    *,
    ctx: PhaseStartContext | None = None,
    agent_name: str | None = None,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> None:
    """Display the start of a pipeline phase."""
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

    if ctx is not None:
        for counter_name, (completed, cap) in ctx.budget_progress.items():
            if cap > 0:
                line.append(
                    f" [{counter_name} {completed + 1}/{cap}]",
                    style="theme.text.muted",
                )
        if (
            ctx.analysis_iteration is not None
            and ctx.max_analysis_iterations is not None
        ):
            suffix = _build_analysis_suffix(
                ctx.analysis_iteration,
                ctx.max_analysis_iterations,
            )
            line.append(suffix, style="theme.text.muted")
        effective_agent = ctx.agent_name or agent_name
    else:
        effective_agent = agent_name

    if effective_agent is not None:
        line.append(f"  agent={effective_agent}", style="theme.text.muted")

    c.print(line)


def show_phase_start_from_state(
    state: object,
    phase: str,
    *,
    display_context: DisplayContext,
) -> None:
    """Display phase start using counters extracted from a pipeline state object."""
    caps_raw: object = getattr(state, "budget_caps", None)
    progress_raw: object = getattr(state, "outer_progress", None)
    agent_raw: object = getattr(state, "agent_name", None)
    caps: dict[str, int] = (
        {str(k): int(v) for k, v in caps_raw.items() if isinstance(v, int)}
        if isinstance(caps_raw, dict)
        else {}
    )
    progress: dict[str, int] = (
        {str(k): int(v) for k, v in progress_raw.items() if isinstance(v, int)}
        if isinstance(progress_raw, dict)
        else {}
    )
    budget_progress: dict[str, tuple[int, int]] = {
        name: (progress.get(name, 0), cap)
        for name, cap in caps.items()
        if cap > 0
    }
    agent_name: str | None = agent_raw if isinstance(agent_raw, str) else None
    ctx = PhaseStartContext(budget_progress=budget_progress, agent_name=agent_name)
    show_phase_start(phase, ctx=ctx, display_context=display_context)




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
