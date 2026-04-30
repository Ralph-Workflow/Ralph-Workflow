"""Policy validation utilities beyond what Pydantic provides at the model level.

These functions perform runtime checks that cross policy boundaries —
for example, verifying that a checkpoint's phase is compatible with
the currently loaded pipeline policy, or that the policy is semantically
complete for policy-driven orchestration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.registry import AgentRegistry
    from ralph.pipeline.state import PipelineState
    from ralph.pipeline.work_units import WorkUnitsPlan
    from ralph.policy.models import PipelinePolicy, PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


class CheckpointPolicyMismatchError(Exception):
    """Raised when a checkpoint's phase is not present in the current policy.

    Attributes:
        checkpoint_phase: Phase name stored in the checkpoint.
        valid_phases: Set of valid phase names in the current policy.
    """

    def __init__(self, checkpoint_phase: str, valid_phases: set[str]) -> None:
        self.checkpoint_phase = checkpoint_phase
        self.valid_phases = valid_phases
        msg = (
            f"Checkpoint was saved at phase '{checkpoint_phase}' which no longer "
            f"exists in pipeline.toml. Valid phases are: {sorted(valid_phases)}. "
            f"Either restore the original pipeline.toml or start fresh with --no-resume."
        )
        super().__init__(msg)


def validate_phase_exists_in_policy(
    phase: str,
    policy: PipelinePolicy,
) -> None:
    """Validate that a phase name is present in the current pipeline policy.

    Args:
        phase: Phase name from checkpoint.
        policy: Currently loaded pipeline policy.

    Raises:
        CheckpointPolicyMismatchError: If the phase is unknown.
    """
    if phase not in policy.phases:
        raise CheckpointPolicyMismatchError(
            checkpoint_phase=phase,
            valid_phases=set(policy.phases.keys()),
        )


def validate_checkpoint_compatible(
    checkpoint_phase: str,
    bundle: PolicyBundle,
) -> None:
    """Validate that a checkpoint phase is compatible with the current policy bundle.

    Args:
        checkpoint_phase: Phase name stored in checkpoint.
        bundle: Currently loaded policy bundle.

    Raises:
        CheckpointPolicyMismatchError: If the checkpoint phase is unknown.
    """
    validate_phase_exists_in_policy(checkpoint_phase, bundle.pipeline)


def validate_drain_bound(
    drain: str,
    bundle: PolicyBundle,
) -> None:
    """Validate that a drain name has a binding in the current policy.

    Args:
        drain: Drain name to check.
        bundle: Currently loaded policy bundle.

    Raises:
        ValueError: If the drain is not bound.
    """
    if drain not in bundle.agents.agent_drains:
        raise ValueError(
            f"Drain '{drain}' is not bound in agents.toml. "
            f"Available drains: {sorted(bundle.agents.agent_drains.keys())}"
        )


def validate_chain_exists(
    chain: str,
    bundle: PolicyBundle,
) -> None:
    """Validate that an agent chain is defined.

    Args:
        chain: Chain name to check.
        bundle: Currently loaded policy bundle.

    Raises:
        ValueError: If the chain is not defined.
    """
    if chain not in bundle.agents.agent_chains:
        raise ValueError(
            f"Chain '{chain}' is not defined in agents.toml. "
            f"Available chains: {sorted(bundle.agents.agent_chains.keys())}"
        )


def validate_drain_contracts(bundle: PolicyBundle) -> None:
    """Validate drain contracts and enforce strict binding rules.

    When forbid_sibling_drain_inference is True, every drain referenced by a
    non-terminal pipeline phase must have an explicit chain binding in agents.toml.
    Required drains are derived from the active pipeline policy, not from a
    hardcoded built-in set — so custom workflows with a subset of canonical drains
    only need to bind the drains they actually use.

    Args:
        bundle: Currently loaded policy bundle.

    Raises:
        PolicyValidationError: If pipeline-used drains lack explicit bindings.
    """
    if not bundle.agents.forbid_sibling_drain_inference:
        return

    # Derive required drains from the active pipeline (non-terminal phases only)
    required_drains: set[str] = {
        phase_def.drain
        for phase_name, phase_def in bundle.pipeline.phases.items()
        if phase_name != bundle.pipeline.terminal_phase and phase_def.role != "terminal"
    }

    unbound_drains: list[str] = [
        drain for drain in required_drains if drain not in bundle.agents.agent_drains
    ]

    if unbound_drains:
        raise PolicyValidationError(
            f"Implicit sibling-drain inference is forbidden, but the following "
            f"pipeline drains lack explicit chain bindings: {sorted(unbound_drains)}. "
            f"Each drain used by a non-terminal pipeline phase must have an explicit "
            f"'chain' binding in agents.toml when forbid_sibling_drain_inference=true."
        )


class PolicyValidationError(Exception):
    """Raised when a policy validation rule is violated.

    Attributes:
        message: Human-readable error message describing the validation failure.
        source: Which policy area failed (optional).
    """

    def __init__(self, message: str, source: str | None = None) -> None:
        self.message = message
        self.source = source
        super().__init__(message)


PolicyViolation = PolicyValidationError


def _validate_terminal_phase(phase_name: str, phase_def: object, errors: list[str]) -> None:
    """Validate constraints on the terminal phase entry."""
    from ralph.policy.models import PhaseDefinition  # noqa: PLC0415

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
    """Validate constraints on analysis-role phases."""
    from ralph.policy.models import PhaseDefinition, PolicyBundle  # noqa: PLC0415

    if not isinstance(phase_def, PhaseDefinition) or not isinstance(bundle, PolicyBundle):
        return

    if phase_def.loop_policy is None:
        errors.append(
            f"phases.{phase_name}: role='analysis' requires loop_policy "
            f"(max_iterations and iteration_state_field)"
        )
    else:
        field = phase_def.loop_policy.iteration_state_field
        # All loop counters must be declared in pipeline.loop_counters
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
            # Check decisions ⊆ vocab: decision keys must be in the artifact vocabulary
            errors.extend(
                f"phases.{phase_name}.decisions.{dk}: "
                f"decision key '{dk}' is not in the artifact "
                f"decision_vocabulary {vocab} for drain '{drain_name}'"
                for dk in phase_def.decisions
                if dk not in vocab
            )
            # Check vocab ⊆ decisions: every vocab entry must have a route
            # No escape hatch — on_failure is for failures, not for unrouted vocab
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
    """Validate constraints on review-role phases.

    review-role phases must declare:
    - issues_outcome: the review_outcome label set when issues are found (required)
    - clean_outcome: the bypass_routes key that signals a clean review (required
      when bypass_routes is non-empty, so the reducer knows which key to look up)
    """
    from ralph.policy.models import PhaseDefinition  # noqa: PLC0415

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
    """Validate that commit_policy.loop_resets references valid iteration fields.

    loop_resets entries must reference iteration_state_field values from analysis
    phases in the policy, or be empty.
    """
    from ralph.policy.models import PhaseDefinition, PipelinePolicy  # noqa: PLC0415

    if not isinstance(phase_def, PhaseDefinition) or not isinstance(policy, PipelinePolicy):
        return
    if phase_def.commit_policy is None:
        return

    loop_resets = phase_def.commit_policy.loop_resets
    if not loop_resets:
        return

    # Collect all iteration_state_field values from analysis phases in the policy
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
    """Validate that commit phases with budget-tracked counters have post_commit_routes.

    When a commit phase increments a budget-tracking counter, at least one
    post_commit_route must apply to that phase so the pipeline can route after commit.
    """
    from ralph.policy.models import PhaseDefinition, PipelinePolicy  # noqa: PLC0415

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

    # At least one post_commit_route must apply to this phase
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
    """Validate constraints on verification-role phases.

    verification-role phases must declare:
    - verification.block: the gating policy (kind, gate_for, on_failure_route)
    - verification.kind: one of 'artifact', 'none'
    - verification.gate_for: one of 'advancement', 'completion', 'release'
    - verification.on_failure_route: either None or a known phase/terminal pseudo-phase
    """
    from ralph.policy.models import PhaseDefinition, PipelinePolicy  # noqa: PLC0415

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


def _validate_recovery_failed_route(
    policy: PipelinePolicy,
    errors: list[str],
) -> None:
    """Validate that recovery.failed_route is consistent with declared terminal phases.

    failed_route must reference a phase declared in pipeline.phases (preferably one
    with role='terminal' and terminal_outcome='failure'). The legacy pseudo-phases
    'phase_failed', 'exit_failure', and the deprecated bare 'failed' alias are no
    longer accepted when 'failed' is not an explicitly declared phase.
    """
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
    # 'failed' is no longer accepted as an undeclared pseudo-phase alias.
    # Declare a phase with role='terminal' and terminal_outcome='failure' and
    # set recovery.failed_route to that phase name.
    if failed_route not in policy.phases:
        errors.append(
            f"recovery.failed_route: '{failed_route}' "
            f"is not a declared phase. Must reference a phase defined in pipeline.phases "
            f"(with role='terminal' and terminal_outcome='failure'). "
            f"Known phases: {sorted(policy.phases.keys())}. "
            f"See docs/sphinx/policy-driven-overhaul-migration.md."
        )


def _collect_reachable_phases(policy: PipelinePolicy) -> set[str]:
    """BFS from entry_phase collecting all reachable phase names."""
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
        candidates: list[str | None] = [t.on_success, t.on_failure, t.on_loopback]
        candidates.extend(phase_def.bypass_routes.values())
        candidates.extend(d.target for d in (phase_def.decisions or {}).values())
        v = phase_def.verification
        if v is not None and v.on_failure_route is not None:
            candidates.append(v.on_failure_route)
        queue.extend(t for t in candidates if t is not None and t in phases and t not in visited)
    return visited


def _validate_reachability(policy: PipelinePolicy, errors: list[str]) -> None:
    """Validate that every declared phase is reachable from entry_phase."""
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
    """Validate that the policy does not rely on removed legacy pseudo-phase constants.

    Flags phases that are named after deprecated pseudo-phase tokens but are not
    properly declared as terminal phases.

    Each error includes the field path, the offending value, and a migration hint.
    """
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


def validate_policy_completeness(bundle: PolicyBundle) -> None:
    """Validate that the policy bundle is semantically complete for policy-driven orchestration.

    Enforces that every non-terminal phase has all the fields required for the runtime
    to drive routing through policy alone, without hidden built-in fallbacks.

    Args:
        bundle: Currently loaded policy bundle.

    Raises:
        PolicyValidationError: If any phase is missing required policy fields.
    """
    errors: list[str] = []
    policy = bundle.pipeline
    terminal_phase = policy.terminal_phase

    for phase_name, phase_def in policy.phases.items():
        if phase_name == terminal_phase or phase_def.role == "terminal":
            _validate_terminal_phase(phase_name, phase_def, errors)
            continue

        # Check role is defined - role is required for all non-terminal phases
        role = phase_def.role
        if role is None:
            errors.append(
                f"phases.{phase_name}: 'role' is required. "
                f"Set role='execution'|'analysis'|'review'|'commit'|'verification'|'terminal'. "
                f"Run `ralph --regenerate-config` to get an updated pipeline.toml template."
            )
            continue

        # Role-specific validation - use separate if statements to help mypy track control flow
        if role == "analysis":
            _validate_analysis_phase(phase_name, phase_def, bundle, errors)

        if role == "review":
            _validate_review_phase(phase_name, phase_def, errors)

        if role == "commit":
            if phase_def.commit_policy is None:
                errors.append(
                    f"phases.{phase_name}: role='commit' requires commit_policy "
                    f"(requires_artifact, increments_counter, loop_resets)"
                )
            else:
                # increments_counter='none' is valid — indicates no outer-progress bump
                # Only flag commit_policy=None (missing entirely), not any specific value
                _validate_commit_phase_loop_resets(phase_name, phase_def, policy, errors)
                _validate_commit_phase_post_commit_routes(phase_name, phase_def, policy, errors)

        if role == "verification":
            _validate_verification_phase(phase_name, phase_def, policy, errors)

    # Validate recovery.failed_route consistency
    _validate_recovery_failed_route(policy, errors)

    # Validate that legacy pseudo-phase tokens are not used
    _validate_no_legacy_phase_constants(policy, errors)

    # Validate that every declared phase is reachable from the entry point
    _validate_reachability(policy, errors)

    if errors:
        raise PolicyValidationError(
            "Policy completeness validation failed:\n"
            + "\n".join(f"  {e}" for e in errors),
            source="completeness",
        )


def get_drain_resolution_matrix(bundle: PolicyBundle) -> dict[str, dict[str, str]]:
    """Generate a normalized drain resolution matrix.

    For each drain, emits a normalized record showing which chain it resolves to,
    enabling explainability and test snapshots.

    Args:
        bundle: Currently loaded policy bundle.

    Returns:
        Dictionary mapping drain names to their resolved chain information.
    """
    matrix: dict[str, dict[str, str]] = {}
    for drain_name in bundle.agents.agent_drains:
        drain_config = bundle.agents.agent_drains[drain_name]
        chain_name = drain_config.chain
        chain_config = bundle.agents.agent_chains.get(chain_name)

        matrix[drain_name] = {
            "chain": chain_name,
            "agents": ",".join(chain_config.agents) if chain_config else "",
            "max_retries": str(chain_config.max_retries) if chain_config else "",
        }
    return matrix


def validate_work_units_against_policy(
    work_units: WorkUnitsPlan,
    pipeline_policy: PipelinePolicy,
    *,
    phase: str,
) -> None:
    """Validate parsed planning work_units against the active phase's parallelization policy.

    Fan-out is transition-scoped: only phases that declare a parallelization block
    can accept multi-work-unit plans. When the active phase has no parallelization
    policy, multi-work-unit plans are rejected fail-closed.

    For plans with multiple work units, also runs the same-workspace overlap check
    (validate_for_same_workspace) as a fail-closed guardrail, in addition to the
    runtime pre-flight check in the runner.

    Args:
        work_units: Parsed work units from planning artifact.
        pipeline_policy: Currently loaded pipeline policy.
        phase: The phase where the work units will execute (e.g., 'development').

    Raises:
        PolicyValidationError: If the plan is unsafe or the phase does not permit fan-out.
    """
    from ralph.pipeline.work_units import (  # noqa: PLC0415
        WorkUnitsValidationError,
        validate_for_same_workspace,
    )

    if len(work_units.work_units) <= 1:
        return

    phase_def = pipeline_policy.phases.get(phase)
    parallel_policy = phase_def.parallelization if phase_def is not None else None

    if parallel_policy is None:
        work_units_count = len(work_units.work_units)
        raise PolicyValidationError(
            f"Phase {phase!r} does not declare parallelization but the plan declares "
            f"{work_units_count} work_units; the active transition policy must explicitly "
            f"enable same-workspace fan-out via [phases.{phase}.parallelization]"
        )

    work_units_count = len(work_units.work_units)

    if work_units_count > parallel_policy.max_work_units:
        raise PolicyViolation(
            f"work_units count {work_units_count} exceeds cap {parallel_policy.max_work_units}"
        )

    if work_units_count > parallel_policy.max_parallel_workers:
        raise PolicyValidationError(
            "Planning artifact declares "
            f"{work_units_count} work_units, exceeding "
            f"max_parallel_workers={parallel_policy.max_parallel_workers}"
        )

    if parallel_policy.require_allowed_directories:
        for unit in work_units.work_units:
            if not unit.allowed_directories:
                raise PolicyValidationError(
                    f"Work unit '{unit.unit_id}' must declare allowed_directories"
                )

    try:
        validate_for_same_workspace(work_units)
    except WorkUnitsValidationError as exc:
        raise PolicyValidationError(str(exc)) from exc


def validate_agent_chains_satisfiable(
    bundle: PolicyBundle,
    agent_registry: AgentRegistry,
) -> None:
    """Validate that every agent referenced in every chain exists in the registry.

    This catches references to unregistered agents at startup rather than
    at runtime. Config consistency check only — not binary presence on PATH.

    Args:
        bundle: Currently loaded policy bundle.
        agent_registry: Populated agent registry to check against.

    Raises:
        PolicyValidationError: If any chain references an unknown agent.
    """
    unknown_agents: list[str] = []
    for chain_name, chain_config in bundle.agents.agent_chains.items():
        unknown_agents.extend(
            f"chain '{chain_name}' references unknown agent '{agent_name}'"
            for agent_name in chain_config.agents
            if agent_registry.get(agent_name) is None
        )
    if unknown_agents:
        raise PolicyValidationError(
            "Agent chains reference unknown agents (check configuration, not PATH): "
            + "; ".join(unknown_agents)
        )


def validate_recovery_config(bundle: PolicyBundle) -> None:
    """Validate recovery-related configuration in the policy bundle.

    Args:
        bundle: Currently loaded policy bundle.

    Raises:
        PolicyValidationError: If recovery config is invalid.
    """
    for chain_name, chain_config in bundle.agents.agent_chains.items():
        if chain_config.max_retries < 0:
            raise PolicyValidationError(
                f"Chain '{chain_name}' has invalid "
                f"max_retries={chain_config.max_retries}; must be >= 0"
            )


def validate_checkpoint_against_policy(
    state: PipelineState,
    bundle: PolicyBundle,
) -> None:
    """Validate a checkpoint state against the current policy bundle.

    Validates the phase exists and, if a drain is set, that it is bound.

    Args:
        state: Pipeline state loaded from checkpoint.
        bundle: Currently loaded policy bundle.

    Raises:
        CheckpointPolicyMismatchError: If the checkpoint phase is unknown.
        PolicyValidationError: If the checkpoint drain is not bound.
    """
    validate_phase_exists_in_policy(state.phase, bundle.pipeline)
    if state.current_drain is not None and state.current_drain not in bundle.agents.agent_drains:
        raise PolicyValidationError(
            f"Checkpoint references drain '{state.current_drain}' which is not bound "
            f"in agents.toml. Available drains: {sorted(bundle.agents.agent_drains.keys())}"
        )


def validate_required_inputs(workspace_scope: WorkspaceScope) -> None:
    """Validate that required input files exist and are readable.

    Checks that PROMPT.md exists in the workspace root, as it is required
    for the pipeline to run.

    Args:
        workspace_scope: The workspace scope containing the root path.

    Raises:
        PolicyValidationError: If required inputs are missing or unreadable.
    """
    prompt_path = workspace_scope.root / "PROMPT.md"
    if not prompt_path.exists():
        raise PolicyValidationError(
            f"Required input file not found: {prompt_path}. "
            "PROMPT.md is the goal/acceptance-criteria document "
            "Ralph Workflow reads as its task input. "
            "Run `ralph --init` to scaffold PROMPT.md and project config files, "
            "then edit PROMPT.md with the task you want Ralph Workflow to run. "
            "New to Ralph Workflow? See docs/sphinx/getting-started.md for a walkthrough."
        )
    if not prompt_path.is_file():
        raise PolicyValidationError(
            f"Required input is not a file: {prompt_path}"
        )
    if not prompt_path.stat().st_size > 0:
        raise PolicyValidationError(
            f"Required input file is empty: {prompt_path}. "
            "Run `ralph --init` to scaffold a starter template, then edit it with your task."
        )
    from ralph.cli.commands.init import STARTER_PROMPT_SENTINEL  # noqa: PLC0415

    content = prompt_path.read_text(encoding="utf-8")
    if STARTER_PROMPT_SENTINEL in content:
        raise PolicyValidationError(
            f"PROMPT.md at {prompt_path} is still the `ralph --init` starter template. "
            "Edit it to describe YOUR task (remove the `<!-- ralph:starter-prompt ... -->` "
            "marker at the top once you have replaced the example content), then re-run `ralph`. "
            "New to Ralph Workflow? See docs/sphinx/getting-started.md for a walkthrough, "
            "or docs/sphinx/concepts.md for what a good PROMPT.md should contain."
        )
