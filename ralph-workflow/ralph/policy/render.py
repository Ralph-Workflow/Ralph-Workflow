"""Render a PolicyExplanation as human-readable text or rich table output.

This module converts the structured PolicyExplanation dataclass (from explain.py)
into text or rich console output that answers:
- What happens after this phase succeeds?
- What makes a phase terminal?
- When is a commit required?
- When does the system retry vs fall back vs fail?
- When is parallel execution allowed?
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.policy.explain import PhaseExplanation, PolicyExplanation


_ROLE_LABELS: dict[str, str] = {
    "execution": "execution (agent runs code)",
    "analysis": "analysis (agent reviews output, decides next step)",
    "review": "review (agent performs code review)",
    "commit": "commit (agent commits changes)",
    "verification": "verification (automated gate)",
    "terminal": "terminal (pipeline ends here)",
    "fanout_join": "fanout-join (waits for parallel workers)",
}


def render_explanation_text(exp: PolicyExplanation) -> str:
    """Render a PolicyExplanation as a multi-section human-readable text string.

    Args:
        exp: The policy explanation to render.

    Returns:
        A multi-line string with sections for phases, counters, and policies.
    """
    lines: list[str] = []

    lines.append("=" * 70)
    lines.append("RALPH WORKFLOW — ACTIVE POLICY EXPLANATION")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Entry phase  : {exp.entry_phase}")
    lines.append(f"Terminal phase: {exp.terminal_phase}")

    if exp.terminal_outcomes:
        lines.append("")
        lines.append("Terminal outcomes:")
        lines.extend(f"  {to.outcome:10s} → {to.phase}" for to in exp.terminal_outcomes)

    lines.append("")

    lines.append("-" * 70)
    lines.append("PHASES")
    lines.append("-" * 70)

    for phase in exp.phases:
        _render_phase_text(phase, lines)

    if exp.loop_counters:
        lines.append("")
        lines.append("-" * 70)
        lines.append("LOOP COUNTERS")
        lines.append("-" * 70)
        for lc in exp.loop_counters:
            desc = f" — {lc.description}" if lc.description else ""
            lines.append(f"  {lc.name}: max={lc.default_max}{desc}")

    if exp.budget_counters:
        lines.append("")
        lines.append("-" * 70)
        lines.append("BUDGET COUNTERS")
        lines.append("-" * 70)
        for bc in exp.budget_counters:
            tracked = "tracked (exhaustion matters)" if bc.tracks_budget else "not tracked"
            desc = f" — {bc.description}" if bc.description else ""
            lines.append(f"  {bc.name}: {tracked}{desc}")

    if exp.parallel_execution is not None:
        _render_parallel_text(exp, lines)

    if exp.recovery is not None:
        _render_recovery_text(exp, lines)

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def _render_parallel_text(exp: PolicyExplanation, lines: list[str]) -> None:
    """Render the parallel execution section."""
    pe = exp.parallel_execution
    if pe is None:
        return
    lines.append("")
    lines.append("-" * 70)
    lines.append("PARALLEL EXECUTION")
    lines.append("-" * 70)
    lines.append(f"  Fanout phase : {pe.phase}")
    lines.append(f"  Max workers  : {pe.max_parallel_workers}")
    lines.append(f"  Max work units: {pe.max_work_units}")
    req = "yes" if pe.require_allowed_directories else "no"
    lines.append(f"  Require allowed_directories: {req}")
    lines.append(
        f"  When is parallel execution allowed? "
        f"When the planning artifact declares multiple work_units "
        f"(up to {pe.max_work_units}) for phase '{pe.phase}'."
    )


def _render_recovery_text(exp: PolicyExplanation, lines: list[str]) -> None:
    """Render the recovery policy section."""
    r = exp.recovery
    if r is None:
        return
    lines.append("")
    lines.append("-" * 70)
    lines.append("RECOVERY POLICY")
    lines.append("-" * 70)
    lines.append(f"  Max recovery cycles : {r.cycle_cap}")
    lines.append(f"  Terminal failure route: {r.terminal_recovery_route}")
    lines.append(
        f"  Session preserved on: {', '.join(r.preserve_session_on_categories) or 'none'}"
    )


def _render_phase_routing(phase: PhaseExplanation, lines: list[str]) -> None:
    """Render routing info for a phase."""
    if phase.terminal_outcome:
        lines.append(f"    Terminal outcome: {phase.terminal_outcome}")
    elif phase.on_success:
        lines.append(f"    On success → {phase.on_success}")
    if phase.on_failure:
        lines.append(f"    On failure → {phase.on_failure}")
    elif not phase.is_terminal:
        lines.append("    On failure → pipeline fails (no on_failure route)")
    if phase.on_loopback:
        lines.append(f"    On loopback → {phase.on_loopback}")

    for outcome, target in phase.bypass_routes.items():
        lines.append(f"    Bypass [{outcome}] → {target}")

    if phase.decisions:
        lines.append("    Decisions:")
        for decision, target in phase.decisions.items():
            lines.append(f"      {decision:20s} → {target}")


def _render_phase_commit(phase: PhaseExplanation, lines: list[str]) -> None:
    """Render commit policy info for a phase."""
    cp = phase.commit_policy
    if cp is None:
        return
    counter = cp.increments_counter
    counter_str = f"increments '{counter}'" if counter else "no counter incremented"
    lines.append(f"    Commit     : {counter_str}")
    if cp.loop_resets:
        lines.append(f"                 resets loop counters: {cp.loop_resets}")
    req = "yes" if cp.requires_artifact else "no"
    lines.append(f"                 requires artifact: {req}")
    lines.append("    When is commit required? When this phase is active and the agent")
    lines.append("      produces changes that need to be committed.")


def _render_phase_text(phase: object, lines: list[str]) -> None:
    """Render a single phase's explanation into text lines."""
    from ralph.policy.explain import PhaseExplanation  # noqa: PLC0415

    if not isinstance(phase, PhaseExplanation):
        return

    badges: list[str] = []
    if phase.is_entry:
        badges.append("ENTRY")
    if phase.is_terminal:
        badges.append("TERMINAL")
    badge_str = f" [{', '.join(badges)}]" if badges else ""

    role_label = _ROLE_LABELS.get(phase.role or "", phase.role or "unknown")
    lines.append("")
    lines.append(f"  Phase: {phase.name}{badge_str}")
    lines.append(f"    Role       : {role_label}")
    lines.append(f"    Drain      : {phase.drain}")

    if phase.chain:
        agent_str = ", ".join(phase.agents) if phase.agents else "(none)"
        lines.append(f"    Chain      : {phase.chain} → agents: [{agent_str}]")
        fallback = ", then fall back to next agent" if len(phase.agents) > 1 else ", then fail"
        lines.append(f"    Retry      : up to {phase.max_retries} retries per agent{fallback}")

    if phase.skip_invocation:
        lines.append("    Invocation : SKIPPED — routing proceeds without invoking an agent")

    _render_phase_routing(phase, lines)

    if phase.loop_policy is not None:
        lp = phase.loop_policy
        lines.append(
            f"    Loop       : counter='{lp.iteration_state_field}', max={lp.max_iterations}"
        )
        if lp.loopback_review_outcome:
            lines.append(
                f"                 loopback sets review_outcome='{lp.loopback_review_outcome}'"
            )

    _render_phase_commit(phase, lines)
