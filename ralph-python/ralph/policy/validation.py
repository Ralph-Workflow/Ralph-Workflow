"""Policy validation utilities beyond what Pydantic provides at the model level.

These functions perform runtime checks that cross policy boundaries —
for example, verifying that a checkpoint's phase is compatible with
the currently loaded pipeline policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.policy.models import DrainName, PipelinePolicy, PolicyBundle


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
