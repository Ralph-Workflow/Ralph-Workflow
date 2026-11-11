
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from ralph.policy._render_text import render_explanation_sentences, render_explanation_text

if TYPE_CHECKING:
    from ralph.policy.explain import ParallelExplanation, PhaseExplanation, PolicyExplanation


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
        next_phase = phase.on_success
        if not next_phase or next_phase in visited:
            break
        current = next_phase

    spine.extend(sorted(pname for pname in phase_map if pname not in visited))

    return spine


def _compute_bfs_order(exp: PolicyExplanation) -> list[str]:
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
        next_phases.extend(t for t in phase.decisions.values() if t and t not in bfs_visited)
        next_phases.sort()
        for np_hop in next_phases:
            if np_hop not in bfs_visited:
                bfs_queue.append(np_hop)
                bfs_visited.add(np_hop)

    bfs_order.extend(pname for pname in phase_map if pname not in bfs_visited)

    return bfs_order


def _compute_box_width(phase_name: str, role: str | None) -> int:
    role_str = f"role={role or 'unknown'}"
    return max(len(phase_name), len(role_str), 6) + 4


def _render_fanout_annotation(
    lines: list[str],
    phase_name: str,
    parallel_phase: str | None,
    pe: ParallelExplanation | None,
) -> None:
    if parallel_phase == phase_name and pe is not None:
        verify = "yes" if pe.post_fanout_verification else "no"
        lines.append(
            f">>> FAN_OUT (max_workers={pe.max_parallel_workers}, "
            f"waves=queued, post_fanout_verify={verify}) >>>"
        )


def _render_loop_annotation(lines: list[str], phase: PhaseExplanation) -> None:
    if phase.loop_policy is not None:
        lp = phase.loop_policy
        lines.append(f"[loop: counter={lp.iteration_state_field}, max={lp.max_iterations}]")


def _render_verification_annotation(lines: list[str], phase: PhaseExplanation) -> None:
    if phase.verification is not None:
        v = phase.verification
        lines.append(f"[verify: kind={v.kind}, gates={v.gate_for}]")


def _render_phase_box(lines: list[str], phase_name: str, role: str | None) -> None:
    width = _compute_box_width(phase_name, role)
    role_str = f"role={role or 'unknown'}"

    lines.append("+" + "-" * (width - 2) + "+")
    name_content = f" {phase_name} "
    lines.append("|" + name_content.center(width - 2) + "|")
    role_content = f" {role_str} "
    lines.append("|" + role_content.center(width - 2) + "|")
    lines.append("+" + "-" * (width - 2) + "+")


def _render_decision_branches(lines: list[str], phase: PhaseExplanation) -> None:
    if not phase.decisions:
        return
    non_success = {k: v for k, v in phase.decisions.items() if v != phase.on_success}
    if not non_success:
        return
    max_len = max(len(k) for k in non_success)
    for decision_name, target in sorted(non_success.items()):
        pad = "-" * (max_len - len(decision_name))
        lines.append(f"    +--[{decision_name}]{pad}--> {target}")


def _render_verification_failure_arrow(lines: list[str], phase: PhaseExplanation) -> None:
    if phase.verification is None:
        return
    v = phase.verification
    if v.on_failure_route and v.on_failure_route != phase.on_failure:
        lines.append(f"    +--[on_failure_route]--> {v.on_failure_route}")


def _render_workflow_fallback_arrow(lines: list[str], phase: PhaseExplanation) -> None:
    if phase.workflow_fallback is None:
        return
    fallback_target, fallback_note = phase.workflow_fallback
    note_str = f" ({fallback_note})" if fallback_note else ""
    lines.append(f"    +--[workflow_fallback]--> {fallback_target}{note_str}")


def _render_loopback_arrow(lines: list[str], phase: PhaseExplanation) -> None:
    if phase.on_loopback and phase.on_loopback != phase.on_success:
        target = phase.on_loopback
        lines.append(f"    <<==[loopback]== returns to '{target}'")
        if phase.loop_policy is not None:
            lp = phase.loop_policy
            lines.append(
                f"    [LOOPBACK: counter={lp.iteration_state_field}, max={lp.max_iterations}]"
            )
        lines.append(f"    >> RE-ENTRY at {target}")


def _render_terminal_marker(lines: list[str], phase: PhaseExplanation) -> None:
    if phase.terminal_outcome == "success":
        lines.append("==SUCCESS==>")
    elif phase.terminal_outcome == "failure":
        lines.append("==FAILURE==>")


def _render_happy_path_arrow(
    lines: list[str], phase: PhaseExplanation, next_phase: str | None
) -> None:
    if phase.on_success and next_phase and not phase.is_terminal and next_phase == phase.on_success:
        lines.append("    |")
        lines.append("    v")


def render_explanation_ascii(exp: PolicyExplanation) -> str:
    phase_map: dict[str, PhaseExplanation] = {p.name: p for p in exp.phases}
    spine_order = _compute_success_spine(exp)
    lines: list[str] = []

    parallel_phases: dict[str, ParallelExplanation] = {
        pe.phase: pe for pe in exp.parallel_executions
    }
    if exp.parallel_execution is not None and exp.parallel_execution.phase not in parallel_phases:
        parallel_phases[exp.parallel_execution.phase] = exp.parallel_execution

    for i, phase_name in enumerate(spine_order):
        phase = phase_map.get(phase_name)
        if phase is None:
            continue

        next_phase = spine_order[i + 1] if i + 1 < len(spine_order) else None

        pe_for_phase = parallel_phases.get(phase_name)
        is_fanout = pe_for_phase is not None
        _render_fanout_annotation(
            lines, phase_name, phase_name if is_fanout else None, pe_for_phase
        )
        _render_loop_annotation(lines, phase)
        _render_verification_annotation(lines, phase)

        if phase.is_entry:
            lines.append("=ENTRY=>")

        _render_phase_box(lines, phase_name, phase.role)
        _render_decision_branches(lines, phase)
        _render_verification_failure_arrow(lines, phase)
        _render_workflow_fallback_arrow(lines, phase)
        _render_loopback_arrow(lines, phase)
        _render_terminal_marker(lines, phase)
        if is_fanout:
            lines.append("+================+")
            lines.append("<<< REJOIN >>>")
        _render_happy_path_arrow(lines, phase, next_phase)

    lines.append("")
    lines.append("Legend:")
    lines.append("  =ENTRY=>           pipeline entry point")
    lines.append("  ==SUCCESS==>       terminal success outcome")
    lines.append("  ==FAILURE==>       terminal failure outcome")
    lines.append("  +--[decision]-->   analysis decision branch")
    lines.append("  <<==[loopback]==   loopback to earlier phase")
    lines.append("  +--[workflow_fallback]--> fallback on chain exhaustion")
    lines.append("  >>> FAN_OUT ...    parallel worker fan-out")
    lines.append("  <<< REJOIN >>>     workers rejoin after fan-out")

    return "\n".join(lines)


__all__ = ["render_explanation_ascii", "render_explanation_sentences", "render_explanation_text"]
