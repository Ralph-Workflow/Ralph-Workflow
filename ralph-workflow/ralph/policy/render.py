"""Render a PolicyExplanation as human-readable text or ASCII workflow diagram.

This module converts the structured PolicyExplanation dataclass (from explain.py)
into text or rich console output that answers:
- What happens after this phase succeeds?
- What makes a phase terminal?
- When is a commit required?
- When does the system retry vs fall back vs fail?
- When is parallel execution allowed?
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.policy.explain import (
        ParallelExplanation,
        PhaseExplanation,
        PolicyExplanation,
    )


_ROLE_LABELS: dict[str, str] = {
    "execution": "execution (agent runs code)",
    "analysis": "analysis (agent reviews output, decides next step)",
    "review": "review (agent performs code review)",
    "commit": "commit (agent commits changes)",
    "verification": "verification (automated gate)",
    "terminal": "terminal (pipeline ends here)",
    "fanout_join": "fanout-join (waits for parallel workers)",
}


def _compute_success_spine(exp: PolicyExplanation) -> list[str]:
    """Compute the happy-path spine by following on_success edges from entry_phase.

    Determinism rule: phase ordering follows on_success edges; ties broken
    alphabetically.
    """
    phase_map: dict[str, PhaseExplanation] = {p.name: p for p in exp.phases}
    spine: list[str] = []
    visited: set[str] = set()

    current = exp.entry_phase
    while current and current not in visited:
        spine.append(current)
        visited.add(current)
        phase = phase_map.get(current)
        if phase is None:
            break
        # Only follow on_success for the spine
        next_phase = phase.on_success
        if not next_phase or next_phase in visited:
            break
        current = next_phase

    # Add any unvisited phases alphabetically (for orphan display)
    spine.extend(sorted(pname for pname in phase_map if pname not in visited))

    return spine


def _compute_bfs_order(exp: PolicyExplanation) -> list[str]:
    """Compute BFS ordering of phases starting from entry_phase.

    Determinism rule: phase ordering follows BFS from entry_phase; ties broken
    alphabetically.
    """
    phase_map: dict[str, PhaseExplanation] = {p.name: p for p in exp.phases}
    bfs_order: list[str] = []
    bfs_queue: deque[str] = deque()
    bfs_visited: set[str] = set()

    if exp.entry_phase not in phase_map:
        return []

    bfs_queue.append(exp.entry_phase)
    bfs_visited.add(exp.entry_phase)

    while bfs_queue:
        current = bfs_queue.popleft()
        bfs_order.append(current)
        phase = phase_map.get(current)
        if phase is None:
            continue

        next_phases: list[str] = []
        if phase.on_success and phase.on_success not in bfs_visited:
            next_phases.append(phase.on_success)
        if phase.on_failure and phase.on_failure not in bfs_visited:
            next_phases.append(phase.on_failure)
        if phase.on_loopback and phase.on_loopback not in bfs_visited:
            next_phases.append(phase.on_loopback)
        next_phases.extend(
            t for t in phase.decisions.values() if t and t not in bfs_visited
        )
        next_phases.sort()
        for np_hop in next_phases:
            if np_hop not in bfs_visited:
                bfs_queue.append(np_hop)
                bfs_visited.add(np_hop)

    bfs_order.extend(pname for pname in phase_map if pname not in bfs_visited)

    return bfs_order


def _compute_box_width(phase_name: str, role: str | None) -> int:
    """Compute the width for a phase box.

    Width = max(len(name), len('role=' + role), 6) + 4.
    """
    role_str = f"role={role or 'unknown'}"
    return max(len(phase_name), len(role_str), 6) + 4


def _render_fanout_annotation(
    lines: list[str],
    phase_name: str,
    parallel_phase: str | None,
    pe: ParallelExplanation | None,
) -> None:
    """Render fanout annotation line if applicable."""
    if parallel_phase == phase_name and pe is not None:
        lines.append(
            f"[fanout: max_workers={pe.max_parallel_workers}, "
            f"max_units={pe.max_work_units}]"
        )


def _render_loop_annotation(lines: list[str], phase: PhaseExplanation) -> None:
    """Render loop annotation line if applicable."""
    if phase.loop_policy is not None:
        lp = phase.loop_policy
        lines.append(
            f"[loop: counter={lp.iteration_state_field}, max={lp.max_iterations}]"
        )


def _render_verification_annotation(lines: list[str], phase: PhaseExplanation) -> None:
    """Render verification gate annotation line if applicable."""
    if phase.verification is not None:
        v = phase.verification
        lines.append(f"[verify: kind={v.kind}, gates={v.gate_for}]")


def _render_phase_box(lines: list[str], phase_name: str, role: str | None) -> None:
    """Render a single phase box with name and role."""
    width = _compute_box_width(phase_name, role)
    role_str = f"role={role or 'unknown'}"

    lines.append("+" + "-" * (width - 2) + "+")
    name_content = f" {phase_name} "
    lines.append("|" + name_content.center(width - 2) + "|")
    role_content = f" {role_str} "
    lines.append("|" + role_content.center(width - 2) + "|")
    lines.append("+" + "-" * (width - 2) + "+")


def _render_decision_branches(lines: list[str], phase: PhaseExplanation) -> None:
    """Render decision branch rows for a phase."""
    if not phase.decisions:
        return
    for decision_name, target in sorted(phase.decisions.items()):
        if target != phase.on_success:
            lines.append(f"    +--[{decision_name}]--> {target}")


def _render_loopback_arrow(lines: list[str], phase: PhaseExplanation) -> None:
    """Render loopback annotation if applicable.

    Emits '<<==[loopback]== returns to TARGET' below the phase box using
    left-pointing arrows so readers cannot mistake it for a forward arrow.
    Adds a [LOOPBACK: counter=..., max=...] annotation when the loopback
    consumes a loop counter (i.e. when loop_policy.loopback_review_outcome is set).
    A '>> RE-ENTRY from SOURCE_loopback' banner is appended to indicate where
    execution re-enters the loop.
    """
    if phase.on_loopback and phase.on_loopback != phase.on_success:
        target = phase.on_loopback
        lines.append(f"    <<==[loopback]== returns to '{target}'")
        if phase.loop_policy is not None and phase.loop_policy.loopback_review_outcome is not None:
            lp = phase.loop_policy
            lines.append(
                f"    [LOOPBACK: counter={lp.iteration_state_field}, max={lp.max_iterations}]"
            )
        lines.append(f"    >> RE-ENTRY from {phase.name}_loopback")


def _render_terminal_marker(lines: list[str], phase: PhaseExplanation) -> None:
    """Render terminal outcome marker if applicable.

    Renders ==SUCCESS==> for phases with terminal_outcome='success'.
    Renders ==FAILURE==> for phases with terminal_outcome='failure'.
    Only actual terminal phases (declared with role='terminal') get markers.
    """
    if phase.terminal_outcome == "success":
        lines.append("==SUCCESS==>")
    elif phase.terminal_outcome == "failure":
        lines.append("==FAILURE==>")


def _render_happy_path_arrow(
    lines: list[str], phase: PhaseExplanation, next_phase: str | None
) -> None:
    """Render happy path arrow to next phase if applicable."""
    if (
        phase.on_success
        and next_phase
        and not phase.is_terminal
        and next_phase == phase.on_success
    ):
        lines.append("    |")
        lines.append("    v")


def render_explanation_ascii(exp: PolicyExplanation) -> str:
    """Render a PolicyExplanation as a deterministic pure-ASCII workflow diagram.

    Visual contract (per PLAN step 4):

    1. BOX STRUCTURE: Each phase renders as a 4-line box:
       Line 1: "+" + "-" * (width-2) + "+"
       Line 2: "|" + <phase_name> centered + "|"
       Line 3: "|" + "role=<role>" centered + "|"
       Line 4: "+" + "-" * (width-2) + "+"
       Width = max(len(name), len("role=" + role), 6) + 4

    2. ENTRY MARKER: =ENTRY=> appears on the line above the entry phase box.

    3. HAPPY-PATH: A center-aligned "|" then "v" arrowhead connects
       consecutive phases on the success spine.

    4. DECISION BRANCHES: For each decision whose target differs from on_success,
       render "    +--[decision_name]--> target_phase" (4-space indent).

    5. LOOPBACK: When on_loopback differs from on_success, render
       "    | loop back to target_phase" and
       "    +---^  (returns to 'target_phase' phase)".
       If loop_policy.loopback_review_outcome is also set, render a third line:
       "    [LOOPBACK: counter=NAME, max=N]".

    6. TERMINAL MARKERS: ==SUCCESS==> for terminal_outcome="success";
       ==FAILURE==> for terminal_outcome="failure". Only policy-declared
       terminal phases get markers.

    7. FANOUT ANNOTATION: [fanout: max_workers=N, max_units=M] appears
       above the phase box for the parallel-eligible phase.

    8. LOOP ANNOTATION: [loop: counter=NAME, max=N] appears above the
       phase box for phases with loop_policy.

    9. GLYPHS: Pure ASCII only — allowed chars: + - | < > v ^ = [ ] _ . ,
       plus alphanumerics. No Unicode box-drawing characters.

    10. ORDERING: Success spine (follows on_success from entry_phase);
        unvisited phases appended alphabetically. Ties broken alphabetically.

    Args:
        exp: The policy explanation to render.

    Returns:
        A multi-line ASCII string representing the workflow diagram.
    """
    phase_map: dict[str, PhaseExplanation] = {p.name: p for p in exp.phases}
    spine_order = _compute_success_spine(exp)
    lines: list[str] = []

    parallel_phase = None
    if exp.parallel_execution is not None:
        parallel_phase = exp.parallel_execution.phase

    for i, phase_name in enumerate(spine_order):
        phase = phase_map.get(phase_name)
        if phase is None:
            continue

        # Determine the next phase on the success spine
        next_phase = spine_order[i + 1] if i + 1 < len(spine_order) else None

        _render_fanout_annotation(
            lines, phase_name, parallel_phase, exp.parallel_execution
        )
        _render_loop_annotation(lines, phase)
        _render_verification_annotation(lines, phase)

        if phase.is_entry:
            lines.append("=ENTRY=>")

        _render_phase_box(lines, phase_name, phase.role)
        _render_decision_branches(lines, phase)
        _render_loopback_arrow(lines, phase)
        _render_terminal_marker(lines, phase)
        _render_happy_path_arrow(lines, phase, next_phase)

    return "\n".join(lines)


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


def _render_explanation_sentences(phase: PhaseExplanation) -> list[str]:
    """Generate explanation sentences for a phase per Required Product Outcome D.

    Produces sentences for decisions, terminal outcomes, bypass_routes, and
    loopback caps.
    """
    sentences: list[str] = []

    if phase.decisions:
        for decision_name, target in phase.decisions.items():
            sentences.append(
                f"Explanation: phase '{phase.name}' routes to "
                f"'{target}' because the configured decision was "
                f"'{decision_name}'."
            )

    if phase.terminal_outcome:
        sentences.append(
            f"Explanation: when reached, the run terminates because "
            f"the workflow policy declares phase '{phase.name}' as a "
            f"terminal '{phase.terminal_outcome}' outcome."
        )

    for outcome, target in sorted(phase.bypass_routes.items()):
        sentences.append(
            f"Explanation: phase '{phase.name}' bypasses to '{target}' "
            f"when the configured outcome is '{outcome}'."
        )

    if phase.on_loopback and phase.loop_policy is not None:
        sentences.append(
            f"Explanation: phase '{phase.name}' loops back to "
            f"'{phase.on_loopback}' until "
            f"{phase.loop_policy.max_iterations} attempts are exhausted, "
            f"after which the run terminates."
        )

    if phase.verification is not None:
        v = phase.verification
        failure_target = v.on_failure_route or "pipeline failure"
        sentences.append(
            f"Explanation: phase '{phase.name}' must satisfy a {v.kind} "
            f"verification gate before {v.gate_for}; failure routes to "
            f"'{failure_target}'."
        )

    return sentences


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


def _render_phase_review(phase: PhaseExplanation, lines: list[str]) -> None:
    """Render review-phase-specific fields for a phase."""
    if phase.role != "review":
        return
    if phase.clean_outcome:
        lines.append(f"    Clean outcome: {phase.clean_outcome}")
    if phase.issues_outcome:
        lines.append(f"    Issues outcome: {phase.issues_outcome}")


def _render_phase_verification(phase: PhaseExplanation, lines: list[str]) -> None:
    """Render verification gate info for a phase."""
    v = phase.verification
    if v is None:
        return
    if v.on_failure_route:
        on_fail_str = f"on_failure_route='{v.on_failure_route}'"
    else:
        on_fail_str = "no on_failure_route (pipeline fails)"
    lines.append(
        f"    Verification: kind={v.kind}, gates={v.gate_for}, {on_fail_str}"
    )
    if v.kind == "artifact":
        lines.append(
            "               An artifact file must be present and non-empty before advancement."
        )
    elif v.kind == "make_target":
        lines.append(
            "               NOT YET IMPLEMENTED — declare kind='artifact' or kind='none'."
        )
    elif v.kind == "none":
        lines.append(
            "               Declarative gate — always passes; use for documentation."
        )


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

    _render_phase_review(phase, lines)

    _render_phase_verification(phase, lines)

    sentences = _render_explanation_sentences(phase)
    lines.extend(sentences)
