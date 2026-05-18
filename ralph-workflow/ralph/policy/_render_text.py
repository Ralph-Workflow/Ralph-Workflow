"""Text rendering helpers for policy explanations."""

from __future__ import annotations

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


def render_explanation_text(exp: object) -> str:
    """Render a PolicyExplanation as a multi-section human-readable text string."""
    if not isinstance(exp, PolicyExplanation):
        return ""

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
            lines.append(f"  {bc.name}: {tracked}, default_max={bc.default_max}{desc}")

    if exp.post_commit_routes:
        _render_post_commit_routes_text(exp, lines)

    if exp.parallel_executions:
        _render_parallel_executions_text(exp, lines)
    elif exp.parallel_execution is not None:
        _render_parallel_text(exp, lines)

    if exp.recovery is not None:
        _render_recovery_text(exp, lines)

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def render_explanation_sentences(phase: PhaseExplanation) -> list[str]:
    """Generate explanation sentences for a phase per Required Product Outcome D."""
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
        if v.on_failure_route:
            sentences.append(
                f"Explanation: phase '{phase.name}' fails verification "
                f"→ routes to '{v.on_failure_route}' because the policy "
                f"declares verification.on_failure_route"
            )

    if not phase.has_parallelization and phase.role not in {"terminal", "fanout_join"}:
        sentences.append(
            f"Explanation: parallel execution is rejected at phase "
            f"'{phase.name}' because no parallelization policy is declared"
        )

    for budget_state, target in phase.post_commit_routes_info:
        sentences.append(
            f"Explanation: after commit phase '{phase.name}' with budget_state "
            f"'{budget_state}' → routes to '{target}' because the workflow "
            f"policy declares this post_commit_route"
        )

    return sentences


def _render_post_commit_routes_text(exp: object, lines: list[str]) -> None:
    if not isinstance(exp, PolicyExplanation):
        return
    lines.append("")
    lines.append("-" * 70)
    lines.append("POST-COMMIT ROUTES")
    lines.append("-" * 70)
    lines.extend(
        f"  phase {route.phase} (budget={route.budget_state}) → {route.target}"
        for route in exp.post_commit_routes
    )


def _render_parallel_executions_text(exp: object, lines: list[str]) -> None:
    if not isinstance(exp, PolicyExplanation):
        return
    lines.append("")
    lines.append("-" * 70)
    lines.append("PARALLEL EXECUTION")
    lines.append("-" * 70)
    for pe in exp.parallel_executions:
        verify = "yes" if pe.post_fanout_verification else "no"
        req = "yes" if pe.require_allowed_directories else "no"
        lines.append(f"  Fanout phase : {pe.phase}")
        lines.append(f"  Max workers  : {pe.max_parallel_workers}")
        lines.append(f"  Max work units: {pe.max_work_units}")
        lines.append(f"  Require allowed_directories: {req}")
        lines.append(f"  post_fanout_verify: {verify}")
        lines.append(
            f"  When is parallel execution allowed? "
            f"When the planning artifact declares multiple work_units "
            f"(up to {pe.max_work_units}) for phase '{pe.phase}'."
        )


def _render_parallel_text(exp: object, lines: list[str]) -> None:
    if not isinstance(exp, PolicyExplanation):
        return
    pe = exp.parallel_execution
    if pe is None:
        return
    lines.append("")
    lines.append("-" * 70)
    lines.append("PARALLEL EXECUTION")
    lines.append("-" * 70)
    verify = "yes" if pe.post_fanout_verification else "no"
    lines.append(f"  Fanout phase : {pe.phase}")
    lines.append(f"  Max workers  : {pe.max_parallel_workers}")
    lines.append(f"  Max work units: {pe.max_work_units}")
    req = "yes" if pe.require_allowed_directories else "no"
    lines.append(f"  Require allowed_directories: {req}")
    lines.append(f"  post_fanout_verify: {verify}")
    lines.append(
        f"  When is parallel execution allowed? "
        f"When the planning artifact declares multiple work_units "
        f"(up to {pe.max_work_units}) for phase '{pe.phase}'."
    )


def _render_recovery_text(exp: object, lines: list[str]) -> None:
    if not isinstance(exp, PolicyExplanation):
        return
    r = exp.recovery
    if r is None:
        return
    lines.append("")
    lines.append("-" * 70)
    lines.append("RECOVERY POLICY")
    lines.append("-" * 70)
    lines.append(f"  Max recovery cycles : {r.cycle_cap}")
    lines.append(f"  Terminal failure route: {r.terminal_recovery_route}")
    lines.append(f"  Session preserved on: {', '.join(r.preserve_session_on_categories) or 'none'}")


def _render_phase_routing(phase: PhaseExplanation, lines: list[str]) -> None:
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

    if phase.workflow_fallback is not None:
        fallback_target, fallback_note = phase.workflow_fallback
        lines.append(f"    Workflow fallback (chain exhausted) → {fallback_target}")
        if fallback_note:
            lines.append(f"      Note: {fallback_note}")

    if phase.decisions:
        lines.append("    Decisions:")
        for decision, target in phase.decisions.items():
            lines.append(f"      {decision:20s} → {target}")


def _render_phase_commit(phase: PhaseExplanation, lines: list[str]) -> None:
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
    if phase.role != "review":
        return
    if phase.clean_outcome:
        lines.append(f"    Clean outcome: {phase.clean_outcome}")
    if phase.issues_outcome:
        lines.append(f"    Issues outcome: {phase.issues_outcome}")


def _render_phase_verification(phase: PhaseExplanation, lines: list[str]) -> None:
    v = phase.verification
    if v is None:
        return
    if v.on_failure_route:
        on_fail_str = f"on_failure_route='{v.on_failure_route}'"
    else:
        on_fail_str = "no on_failure_route (pipeline fails)"
    lines.append(f"    Verification: kind={v.kind}, gates={v.gate_for}, {on_fail_str}")
    if v.kind == "artifact":
        lines.append(
            "               An artifact file must be present and non-empty before advancement."
        )
    elif v.kind == "none":
        lines.append("               Declarative gate — always passes; use for documentation.")


def _render_phase_text(phase: object, lines: list[str]) -> None:
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
    lines.extend(render_explanation_sentences(phase))
