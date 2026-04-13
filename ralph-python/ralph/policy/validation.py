"""Policy validation utilities beyond what Pydantic provides at the model level.

These functions perform runtime checks that cross policy boundaries —
for example, verifying that a checkpoint's phase is compatible with
the currently loaded pipeline policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy, PolicyBundle


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
