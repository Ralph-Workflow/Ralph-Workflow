"""Phase transition display for Ralph pipeline.

Renders visually distinct banners and separators at pipeline phase boundaries
so the user can easily follow the flow of planning → development → review → …
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

_PHASE_STYLES: dict[str, str] = {
    "planning": "cyan",
    "development": "green",
    "development_analysis": "magenta",
    "development_commit": "blue",
    "review": "yellow",
    "review_analysis": "magenta",
    "review_commit": "blue",
    "fix": "red",
    "complete": "bold green",
    "failed": "bold red",
}

_MAJOR_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("planning", "development"),
        ("development", "review"),
        ("review", "complete"),
        ("fix", "review"),
        ("development_commit", "review"),
        ("review_commit", "complete"),
    }
)


def _phase_style(phase: str) -> str:
    """Return the rich style string for a phase name."""
    return _PHASE_STYLES.get(phase, "dim")


def _phase_label(phase: str) -> str:
    """Return a human-readable label for a phase name.

    Examples:
        >>> _phase_label("development_analysis")
        'Development Analysis'
        >>> _phase_label("review_commit")
        'Review Commit'
    """
    return phase.replace("_", " ").title()


def show_phase_transition(
    from_phase: str,
    to_phase: str,
    *,
    context: dict[str, object] | None = None,
    console: Console | None = None,
) -> None:
    """Display a visual transition between pipeline phases.

    Major transitions (e.g. planning → development) get a prominent banner.
    Minor transitions (e.g. development → development_analysis) get a simple rule.

    Args:
        from_phase: The phase being left.
        to_phase: The phase being entered.
        context: Optional key-value context to display alongside the transition.
        console: Rich console for output.
    """
    c = console or Console()

    style = _phase_style(to_phase)
    from_label = _phase_label(from_phase)
    to_label = _phase_label(to_phase)

    is_major = (from_phase, to_phase) in _MAJOR_TRANSITIONS

    if is_major:
        c.print()
        c.print(Rule(style=style))
        banner = Text()
        banner.append(f"  {from_label}", style="dim")
        banner.append(" → ", style="bold")
        banner.append(to_label, style=f"bold {style}")
        if context:
            detail = "  ".join(f"{k}={v}" for k, v in context.items())
            banner.append(f"  ({detail})", style="dim")
        c.print(banner)
        c.print(Rule(style=style))
    else:
        c.print()
        title = Text()
        title.append(f"{from_label} → {to_label}")
        c.print(Rule(title=title, style=style))


@dataclass(frozen=True)
class PhaseStartContext:
    """Optional counters and metadata for phase start display."""

    iteration: int | None = None
    total_iterations: int | None = None
    reviewer_pass: int | None = None
    total_reviewer_passes: int | None = None
    agent_name: str | None = None


def show_phase_start(
    phase: str,
    *,
    ctx: PhaseStartContext | None = None,
    agent_name: str | None = None,
    console: Console | None = None,
) -> None:
    """Display the start of a pipeline phase.

    Args:
        phase: Phase name.
        ctx: Optional context with iteration/reviewer counters.
        agent_name: Name of the agent being invoked (shortcut; also settable via ctx).
        console: Rich console for output.
    """
    c = console or Console()
    style = _phase_style(phase)
    label = _phase_label(phase)

    line = Text()
    line.append("▶ ", style=f"bold {style}")
    line.append(label, style=f"bold {style}")

    if ctx is not None:
        if ctx.iteration is not None and ctx.total_iterations is not None:
            line.append(f" [iteration {ctx.iteration + 1}/{ctx.total_iterations}]", style="dim")
        if ctx.reviewer_pass is not None and ctx.total_reviewer_passes is not None:
            line.append(f" [pass {ctx.reviewer_pass + 1}/{ctx.total_reviewer_passes}]", style="dim")
        effective_agent = ctx.agent_name or agent_name
    else:
        effective_agent = agent_name

    if effective_agent is not None:
        line.append(f"  agent={effective_agent}", style="dim")

    c.print(line)


def show_phase_complete(
    phase: str,
    *,
    decision: str | None = None,
    console: Console | None = None,
) -> None:
    """Display phase completion with an optional decision outcome.

    Args:
        phase: Phase that completed.
        decision: Optional decision (e.g. 'approved', 'needs changes').
        console: Rich console for output.
    """
    c = console or Console()
    style = _phase_style(phase)
    label = _phase_label(phase)

    line = Text()
    line.append("✓ ", style=f"bold {style}")
    line.append(f"{label} complete", style=style)
    if decision is not None:
        line.append(f" — {decision}", style="bold")

    c.print(line)
