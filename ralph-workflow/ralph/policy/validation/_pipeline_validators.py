
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.policy.models._phase_definition import PhaseDefinition
    from ralph.policy.models._pipeline_policy import PipelinePolicy
    from ralph.policy.models._post_commit_route import PostCommitRoute


def _validate_recovery_failed_route(
    policy: PipelinePolicy,
    errors: list[str],
) -> None:
    failed_route = policy.recovery.failed_route
    if failed_route in ("phase_failed", "exit_failure"):
        errors.append(
            f"recovery.failed_route: '{failed_route}' is no longer supported. "
            f"Declare a terminal failure phase with role='terminal' and "
            f"terminal_outcome='failure' and reference it via recovery.failed_route "
            f"(and optionally recovery.terminal_failure_phase). "
            f"See docs/sphinx/policy-driven-overhaul-migration.md."
        )
        return
    if failed_route not in policy.phases:
        errors.append(
            f"recovery.failed_route: '{failed_route}' "
            f"is not a declared phase. Must reference a phase defined in pipeline.phases "
            f"(with role='terminal' and terminal_outcome='failure'). "
            f"Known phases: {sorted(policy.phases.keys())}. "
            f"See docs/sphinx/policy-driven-overhaul-migration.md."
        )


def _collect_reachable_phases(policy: PipelinePolicy) -> set[str]:
    phases = policy.phases
    visited: set[str] = set()
    queue: list[str] = [policy.entry_phase]
    while queue:
        current = queue.pop()
        if current in visited or current not in phases:
            continue
        visited.add(current)
        phase_def = phases[current]
        t = phase_def.transitions
        candidates: list[str | None] = [t.on_success, t.on_failure, t.on_loopback, t.on_reject]
        candidates.extend(phase_def.bypass_routes.values())
        candidates.extend(d.target for d in (phase_def.decisions or {}).values())
        candidates.extend(
            route.target for route in policy.post_commit_routes if route.when.phase == current
        )
        v = phase_def.verification
        if v is not None and v.on_failure_route is not None:
            candidates.append(v.on_failure_route)
        queue.extend(t for t in candidates if t is not None and t in phases and t not in visited)
    return visited


def _validate_reachability(policy: PipelinePolicy, errors: list[str]) -> None:
    if policy.entry_phase not in policy.phases:
        return
    reachable = _collect_reachable_phases(policy)
    unreachable = sorted(name for name in policy.phases if name not in reachable)
    if unreachable:
        errors.append(
            f"Unreachable phases detected (not reachable from entry_phase "
            f"'{policy.entry_phase}'): {unreachable}. "
            f"Remove these phases or add transitions leading to them."
        )


def _validate_no_legacy_phase_constants(
    policy: PipelinePolicy,
    errors: list[str],
) -> None:
    for phase_name in ("phase_failed", "exit_failure"):
        if phase_name in policy.phases:
            phase_def = policy.phases[phase_name]
            if phase_def.role != "terminal" or phase_def.terminal_outcome != "failure":
                errors.append(
                    f"phases.{phase_name}: this name is a legacy pseudo-phase token. "
                    f"If you intended a terminal failure phase, set role='terminal' and "
                    f"terminal_outcome='failure'. "
                    f"See docs/sphinx/policy-driven-overhaul-migration.md."
                )


def _validate_shared_drain_history_consistency(
    policy: PipelinePolicy,
    errors: list[str],
) -> None:
    drain_enabled: dict[str, bool] = {}
    for phase_name, phase_def in policy.phases.items():
        if phase_def.artifact_history is None:
            continue
        drain = phase_def.drain
        enabled = phase_def.artifact_history.enabled
        if drain in drain_enabled:
            if drain_enabled[drain] != enabled:
                errors.append(
                    f"phases.{phase_name}: artifact_history.enabled={enabled} conflicts with "
                    f"another phase that shares drain '{drain}' and declares "
                    f"artifact_history.enabled={drain_enabled[drain]}. "
                    f"Phases sharing a drain must agree on artifact_history.enabled because "
                    f"the runtime cannot distinguish between phases at artifact-submit time."
                )
        else:
            drain_enabled[drain] = enabled


def _route_budget_counter(
    policy: PipelinePolicy,
    phase_name: str,
    phase_def: PhaseDefinition,
) -> str | None:
    lifecycle = policy.lifecycle_phases.get(phase_name)
    if lifecycle is not None:
        counter = lifecycle.increments_counter
        return str(counter) if counter else None
    commit_policy = phase_def.commit_policy
    if commit_policy is None:
        return None
    counter = commit_policy.route_counter or commit_policy.increments_counter
    return str(counter) if counter else None


def _validate_spent_budget_can_end_the_run(
    policy: PipelinePolicy,
    phase_name: str,
    counter: str,
    errors: list[str],
) -> None:
    terminals = policy.terminal_states()
    for state_name in ("exhausted", "no_review"):
        routes = [
            route
            for route in policy.post_commit_routes
            if route.when.phase == phase_name and route.when.budget_state == state_name
        ]
        if not routes:
            continue
        for outcome in sorted({route.when.cycle_outcome for route in routes}, key=str):
            target = _routed_target(routes, outcome)
            if target is None or _terminal_is_reachable(policy, target, terminals, outcome=outcome):
                continue
            errors.append(
                f"phases.{phase_name}: with budget_state='{state_name}' and cycle outcome "
                f"'{outcome}', post-commit routing goes to '{target}', from which no terminal "
                f"is reachable without failing. Once counter '{counter}' is spent the run "
                "would re-enter the cycle forever. Route that combination to a terminal, or to "
                "a phase from which one is reachable."
            )


_UNRECORDED_FALLBACK_OUTCOME = "completed"


def _routed_target(routes: Sequence[PostCommitRoute], outcome: str | None) -> str | None:
    candidates = (outcome,) if outcome is not None else (None, _UNRECORDED_FALLBACK_OUTCOME)
    for candidate in candidates:
        for route in routes:
            if route.when.cycle_outcome is None or route.when.cycle_outcome == candidate:
                return route.target
    return None


def _terminal_is_reachable(
    policy: PipelinePolicy,
    start: str,
    terminals: set[str],
    *,
    outcome: str | None = None,
) -> bool:
    seen: set[str] = set()
    frontier = [start]
    while frontier:
        phase = frontier.pop()
        if phase in terminals:
            return True
        if phase in seen or phase not in policy.phases:
            continue
        seen.add(phase)
        phase_def = policy.phases[phase]
        phase_routes = [route for route in policy.post_commit_routes if route.when.phase == phase]
        routed = _routed_target(phase_routes, outcome) if phase_routes else None
        if routed is not None:
            frontier.append(routed)
        elif phase_def.transitions.on_success is not None:
            frontier.append(phase_def.transitions.on_success)
        frontier.extend(route.target for route in phase_def.decisions.values())
    return False


def _validate_post_commit_routes_complete(
    policy: PipelinePolicy,
    errors: list[str],
) -> None:
    required_states = {"remaining", "exhausted", "no_review"}

    for phase_name, phase_def in policy.phases.items():
        if phase_def.role != "commit":
            continue
        counter = _route_budget_counter(policy, phase_name, phase_def)
        if not counter or counter == "none":
            continue
        counter_cfg = policy.budget_counters.get(counter)
        if counter_cfg is None or not counter_cfg.tracks_budget:
            continue

        declared_states = {
            r.when.budget_state for r in policy.post_commit_routes if r.when.phase == phase_name
        }
        missing = required_states - declared_states
        if missing:
            errors.append(
                f"phases.{phase_name}: increments budget-tracking counter '{counter}' "
                f"but post_commit_routes do not cover all budget states. "
                f"Missing: {sorted(missing)}. "
                f"Add [[post_commit_routes]] entries with when.phase='{phase_name}' "
                f"for each missing budget_state. "
                f"See docs/sphinx/policy-driven-overhaul-migration.md."
            )
        _validate_spent_budget_can_end_the_run(policy, phase_name, counter, errors)


def _validate_tracked_counters_have_positive_max(
    policy: PipelinePolicy,
    errors: list[str],
) -> None:
    for counter_name, counter_cfg in policy.budget_counters.items():
        if counter_cfg.tracks_budget and counter_cfg.default_max == 0:
            errors.append(
                f"budget_counters.{counter_name}: tracks_budget=True with default_max=0 "
                f"means the pipeline cannot start (budget exhausted before any cycle "
                f"completes). Set default_max > 0 for tracked counters, or set "
                f"tracks_budget=False if this counter should not gate advancement. "
                f"See [budget_counters.{counter_name}].default_max in pipeline.toml."
            )


def _validate_terminal_failure_phase_declared(
    policy: PipelinePolicy,
    errors: list[str],
) -> None:
    has_failure_route = any(
        phase_def.transitions.on_failure is not None
        or (
            phase_def.verification is not None
            and phase_def.verification.on_failure_route is not None
        )
        for phase_def in policy.phases.values()
    )
    if not has_failure_route:
        return

    has_terminal_failure = any(
        phase_def.role == "terminal" and phase_def.terminal_outcome == "failure"
        for phase_def in policy.phases.values()
    )
    if not has_terminal_failure:
        errors.append(
            "Policy declares on_failure or verification.on_failure_route transitions "
            "but no phase has role='terminal' and terminal_outcome='failure'. "
            "Add a terminal failure phase so the runtime routes failures to a "
            "policy-declared destination instead of a hidden built-in fallback. "
            "See docs/sphinx/policy-driven-overhaul-migration.md."
        )


def _validate_review_phase_outcome_complete(
    policy: PipelinePolicy,
    errors: list[str],
) -> None:
    for phase_name, phase_def in policy.phases.items():
        if phase_def.role != "review":
            continue
        if phase_def.clean_outcome is None:
            continue
        if phase_def.clean_outcome not in phase_def.bypass_routes:
            errors.append(
                f"phases.{phase_name}: clean_outcome='{phase_def.clean_outcome}' "
                f"is not a key in bypass_routes. "
                f"Add bypass_routes.{phase_def.clean_outcome} = '<target_phase>' "
                f"so the runtime can route on a clean review. "
                f"See docs/sphinx/policy-driven-overhaul-migration.md."
            )


def _validate_commit_review_policy(
    policy: PipelinePolicy,
    errors: list[str],
) -> None:
    cr = policy.commit_review
    if cr.max_deleted_files < 0:
        errors.append(
            f"commit_review.max_deleted_files: must be non-negative (got {cr.max_deleted_files})"
        )
    if not (0.0 <= cr.max_deleted_fraction <= 1.0):
        errors.append(
            f"commit_review.max_deleted_fraction: must be between 0.0 and 1.0 (got {cr.max_deleted_fraction})"
        )
    if cr.binary_size_limit <= 0:
        errors.append(
            f"commit_review.binary_size_limit: must be positive (got {cr.binary_size_limit})"
        )
    if cr.proposal_retention < 1:
        errors.append(
            f"commit_review.proposal_retention: must be at least 1 (got {cr.proposal_retention})"
        )
    if cr.max_rejections < 1:
        errors.append(f"commit_review.max_rejections: must be at least 1 (got {cr.max_rejections})")

    if "commit_review_rejections" in policy.loop_counters:
        counter = policy.loop_counters["commit_review_rejections"]
        if counter.default_max < 1:
            errors.append(
                f"loop_counters.commit_review_rejections: default_max must be at least 1 (got {counter.default_max})"
            )
