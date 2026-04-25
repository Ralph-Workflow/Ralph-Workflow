"""Policy validation utilities beyond what Pydantic provides at the model level.

These functions perform runtime checks that cross policy boundaries —
for example, verifying that a checkpoint's phase is compatible with
the currently loaded pipeline policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.registry import AgentRegistry
    from ralph.pipeline.state import PipelineState
    from ralph.pipeline.work_units import WorkUnitsPlan
    from ralph.policy.models import DrainName, PipelinePolicy, PolicyBundle
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

    This validates rule #10 from the orchestration spec: any config that relies
    on implicit sibling-drain inference is rejected when forbid_sibling_drain_inference
    is True.

    Args:
        bundle: Currently loaded policy bundle.

    Raises:
        PolicyValidationError: If sibling-drain inference is detected and forbidden.
    """
    if not bundle.agents.forbid_sibling_drain_inference:
        return

    # Built-in drains that should have explicit bindings
    # Note: "complete" is a terminal marker phase, not an actual drain that invokes
    # an agent, so it doesn't need a chain binding
    built_in_drains: set[DrainName] = {
        "planning",
        "development",
        "development_analysis",
        "development_commit",
        "review",
        "review_analysis",
        "review_commit",
        "fix",
    }

    unbound_drains: list[str] = [
        drain for drain in built_in_drains if drain not in bundle.agents.agent_drains
    ]

    if unbound_drains:
        raise PolicyValidationError(
            f"Implicit sibling-drain inference is forbidden, but the following "
            f"drains lack explicit chain bindings: {sorted(unbound_drains)}. "
            f"Each built-in drain must have an explicit 'chain' binding in "
            f"agents.toml when forbid_sibling_drain_inference=true."
        )


class PolicyValidationError(Exception):
    """Raised when a policy validation rule is violated.

    Attributes:
        message: Human-readable error message describing the validation failure.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


PolicyViolation = PolicyValidationError


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
) -> None:
    """Validate parsed planning work_units against pipeline parallel policy."""
    parallel_policy = pipeline_policy.parallel_execution
    if len(work_units.work_units) <= 1:
        return

    if parallel_policy is None:
        raise PolicyValidationError(
            "Planning artifact declares multiple work_units but "
            "pipeline.parallel_execution is not configured"
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
            "then edit PROMPT.md with the task you want Ralph Workflow to run."
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
            "See docs/sphinx/concepts.md for what a good PROMPT.md should contain."
        )
