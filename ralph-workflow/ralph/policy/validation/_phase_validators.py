"""Phase-level validation helpers for policy completeness checks."""

from __future__ import annotations

from ralph.policy.models._phase_definition import PhaseDefinition
from ralph.policy.models._pipeline_policy import PipelinePolicy
from ralph.policy.models._policy_bundle import PolicyBundle


def _validate_terminal_phase(phase_name: str, phase_def: object, errors: list[str]) -> None:
    if not isinstance(phase_def, PhaseDefinition):
        return
    if phase_def.role is not None and phase_def.role != "terminal":
        errors.append(
            f"phases.{phase_name}: terminal_phase must have role='terminal' "
            f"(got role='{phase_def.role}')"
        )
    if phase_def.role == "terminal" and phase_def.terminal_outcome is None:
        errors.append(
            f"phases.{phase_name}: role='terminal' requires terminal_outcome "
            f"to be set ('success' or 'failure')"
        )


def _validate_analysis_phase(
    phase_name: str,
    phase_def: object,
    bundle: object,
    errors: list[str],
) -> None:
    if not isinstance(phase_def, PhaseDefinition) or not isinstance(bundle, PolicyBundle):
        return

    if phase_def.loop_policy is None:
        errors.append(
            f"phases.{phase_name}: role='analysis' requires loop_policy (iteration_state_field)"
        )
    else:
        field = phase_def.loop_policy.iteration_state_field
        if field not in bundle.pipeline.loop_counters:
            errors.append(
                f"phases.{phase_name}.loop_policy.iteration_state_field: "
                f"'{field}' is not declared in pipeline.loop_counters. "
                f"Add [loop_counters.{field}] to pipeline.toml to declare this counter."
            )
    if not phase_def.decisions:
        errors.append(
            f"phases.{phase_name}: role='analysis' requires at least one entry "
            f"in decisions (maps decision vocabulary to routing targets)"
        )
    else:
        drain_name = phase_def.drain
        vocab: list[str] = []
        for art in bundle.artifacts.artifacts.values():
            if art.drain == drain_name and art.decision_vocabulary:
                vocab.extend(art.decision_vocabulary)
        if vocab:
            errors.extend(
                f"phases.{phase_name}.decisions.{dk}: "
                f"decision key '{dk}' is not in the artifact "
                f"decision_vocabulary {vocab} for drain '{drain_name}'"
                for dk in phase_def.decisions
                if dk not in vocab
            )
            uncovered = [v for v in vocab if v not in phase_def.decisions]
            errors.extend(
                f"phases.{phase_name}.decisions: vocab entry '{v}' has no route "
                f"in decisions. Every decision_vocabulary entry must have an "
                f"explicit route in the decisions table."
                for v in uncovered
            )


def _validate_review_phase(
    phase_name: str,
    phase_def: object,
    errors: list[str],
) -> None:
    if not isinstance(phase_def, PhaseDefinition):
        return
    if phase_def.issues_outcome is None:
        errors.append(
            f"phases.{phase_name}: role='review' requires issues_outcome "
            f"(the review_outcome label set when issues are found, e.g. 'has_issues'). "
            f"See docs/migration/policy-v2.md."
        )
    if phase_def.bypass_routes and phase_def.clean_outcome is None:
        errors.append(
            f"phases.{phase_name}: role='review' with bypass_routes requires clean_outcome "
            f"(the bypass_routes key that signals a clean review, e.g. 'clean'). "
            f"See docs/migration/policy-v2.md."
        )


def _validate_commit_phase_loop_resets(
    phase_name: str,
    phase_def: object,
    policy: object,
    errors: list[str],
) -> None:
    if not isinstance(phase_def, PhaseDefinition) or not isinstance(policy, PipelinePolicy):
        return
    if phase_def.commit_policy is None:
        return

    loop_resets = phase_def.commit_policy.loop_resets
    if not loop_resets:
        return

    valid_iteration_fields: set[str] = set()
    for defn in policy.phases.values():
        lp = defn.loop_policy
        if isinstance(defn, PhaseDefinition) and defn.role == "analysis" and lp is not None:
            valid_iteration_fields.add(lp.iteration_state_field)

    invalid_resets = [f for f in loop_resets if f not in valid_iteration_fields]
    if invalid_resets:
        errors.append(
            f"phases.{phase_name}.commit_policy.loop_resets: "
            f"invalid iteration field(s) {invalid_resets}. "
            f"loop_resets must reference iteration_state_field values from analysis phases "
            f"or be empty. Valid fields: {sorted(valid_iteration_fields)}"
        )


def _validate_commit_phase_post_commit_routes(
    phase_name: str,
    phase_def: object,
    policy: object,
    errors: list[str],
) -> None:
    if not isinstance(phase_def, PhaseDefinition) or not isinstance(policy, PipelinePolicy):
        return
    if phase_def.commit_policy is None:
        return

    counter = phase_def.commit_policy.increments_counter
    if counter == "none" or counter not in policy.budget_counters:
        return

    counter_config = policy.budget_counters[counter]
    if not counter_config.tracks_budget:
        return

    applies = any(r.when.phase == phase_name for r in policy.post_commit_routes)
    if not applies:
        errors.append(
            f"phases.{phase_name}: increments budget-tracking counter '{counter}' "
            f"but no post_commit_routes apply to this phase. "
            f"Add at least one [[post_commit_routes]] entry with when.phase='{phase_name}'."
        )


def _validate_verification_phase(
    phase_name: str,
    phase_def: object,
    policy: object,
    errors: list[str],
) -> None:
    if not isinstance(phase_def, PhaseDefinition) or not isinstance(policy, PipelinePolicy):
        return
    if phase_def.verification is None:
        errors.append(
            f"phases.{phase_name}: role='verification' requires a verification block "
            f"(kind, gate_for, on_failure_route). Add [phases.{phase_name}.verification] "
            f"to pipeline.toml. See docs/sphinx/policy-explanation.md."
        )
        return

    kind = phase_def.verification.kind
    if kind not in ("artifact", "none"):
        errors.append(
            f"phases.{phase_name}.verification.kind: must be one of "
            f"'artifact', 'none' (got '{kind}'). "
            f"Note: 'make_target' has been removed; use kind='artifact' with a "
            f"verification artifact or kind='none'. "
            f"See docs/sphinx/policy-driven-overhaul-migration.md."
        )

    gate_for = phase_def.verification.gate_for
    if gate_for not in ("advancement", "completion", "release"):
        errors.append(
            f"phases.{phase_name}.verification.gate_for: must be one of "
            f"'advancement', 'completion', 'release' (got '{gate_for}')"
        )

    on_failure_route = phase_def.verification.on_failure_route
    if on_failure_route is not None:
        known_phases = sorted(policy.phases.keys())
        if on_failure_route not in policy.phases:
            errors.append(
                f"phases.{phase_name}.verification.on_failure_route: "
                f"'{on_failure_route}' is not a declared phase. "
                f"Declare a phase with role='terminal' and terminal_outcome='failure' "
                f"and reference it here. "
                f"Known phases: {known_phases}. "
                f"See docs/sphinx/policy-driven-overhaul-migration.md."
            )


def _validate_skip_invocation_has_on_success(
    phase_name: str, phase_def: object, errors: list[str]
) -> None:
    if not isinstance(phase_def, PhaseDefinition):
        return
    if phase_def.skip_invocation and not phase_def.transitions.on_success:
        errors.append(
            f"phases.{phase_name}: skip_invocation=true requires transitions.on_success "
            f"to be set so routing can proceed without invoking an agent. "
            f"Add on_success = '<target_phase>' under [phases.{phase_name}.transitions]."
        )


def _validate_parallelization_consistency(
    phase_name: str, phase_def: object, errors: list[str]
) -> None:
    if not isinstance(phase_def, PhaseDefinition):
        return
    para = phase_def.parallelization
    if para is None:
        return
    if para.max_work_units < para.max_parallel_workers:
        errors.append(
            f"phases.{phase_name}: parallelization.max_work_units ({para.max_work_units}) "
            f"must be >= parallelization.max_parallel_workers ({para.max_parallel_workers}). "
            f"The runtime caps workers to max_work_units, so declaring more workers than "
            f"work units makes the policy misleading. "
            f"Increase max_work_units or decrease max_parallel_workers."
        )
