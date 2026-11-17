from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.policy.loader import load_policy, load_policy_or_die
    from ralph.policy.models import (
        AgentChainConfig,
        AgentDrainConfig,
        AgentsPolicy,
        ArtifactContract,
        ArtifactsPolicy,
        CommitReviewPolicy,
        DrainName,
        PhaseDefinition,
        PhaseTransition,
        PipelinePolicy,
        PolicyBundle,
    )
    from ralph.policy.validation import (
        CheckpointPolicyMismatchError,
        PolicyValidationError,
        validate_chain_exists,
        validate_checkpoint_compatible,
        validate_drain_bound,
        validate_phase_exists_in_policy,
        validate_policy_completeness,
    )

__all__ = [
    "AgentChainConfig",
    "AgentDrainConfig",
    "AgentsPolicy",
    "ArtifactContract",
    "ArtifactsPolicy",
    "CheckpointPolicyMismatchError",
    "CommitReviewPolicy",
    "DrainName",
    "PhaseDefinition",
    "PhaseTransition",
    "PipelinePolicy",
    "PolicyBundle",
    "PolicyValidationError",
    "load_policy",
    "load_policy_or_die",
    "validate_chain_exists",
    "validate_checkpoint_compatible",
    "validate_drain_bound",
    "validate_phase_exists_in_policy",
    "validate_policy_completeness",
]


def _load_AgentChainConfig() -> object:
    from ralph.policy.models import AgentChainConfig
    return AgentChainConfig

def _load_AgentDrainConfig() -> object:
    from ralph.policy.models import AgentDrainConfig
    return AgentDrainConfig

def _load_AgentsPolicy() -> object:
    from ralph.policy.models import AgentsPolicy
    return AgentsPolicy

def _load_ArtifactContract() -> object:
    from ralph.policy.models import ArtifactContract
    return ArtifactContract

def _load_ArtifactsPolicy() -> object:
    from ralph.policy.models import ArtifactsPolicy
    return ArtifactsPolicy

def _load_CheckpointPolicyMismatchError() -> object:
    from ralph.policy.validation import CheckpointPolicyMismatchError
    return CheckpointPolicyMismatchError

def _load_CommitReviewPolicy() -> object:
    from ralph.policy.models import CommitReviewPolicy
    return CommitReviewPolicy

def _load_DrainName() -> object:
    from ralph.policy.models import DrainName
    return DrainName

def _load_PhaseDefinition() -> object:
    from ralph.policy.models import PhaseDefinition
    return PhaseDefinition

def _load_PhaseTransition() -> object:
    from ralph.policy.models import PhaseTransition
    return PhaseTransition

def _load_PipelinePolicy() -> object:
    from ralph.policy.models import PipelinePolicy
    return PipelinePolicy

def _load_PolicyBundle() -> object:
    from ralph.policy.models import PolicyBundle
    return PolicyBundle

def _load_PolicyValidationError() -> object:
    from ralph.policy.validation import PolicyValidationError
    return PolicyValidationError

def _load_load_policy() -> object:
    from ralph.policy.loader import load_policy
    return load_policy

def _load_load_policy_or_die() -> object:
    from ralph.policy.loader import load_policy_or_die
    return load_policy_or_die

def _load_validate_chain_exists() -> object:
    from ralph.policy.validation import validate_chain_exists
    return validate_chain_exists

def _load_validate_checkpoint_compatible() -> object:
    from ralph.policy.validation import validate_checkpoint_compatible
    return validate_checkpoint_compatible

def _load_validate_drain_bound() -> object:
    from ralph.policy.validation import validate_drain_bound
    return validate_drain_bound

def _load_validate_phase_exists_in_policy() -> object:
    from ralph.policy.validation import validate_phase_exists_in_policy
    return validate_phase_exists_in_policy

def _load_validate_policy_completeness() -> object:
    from ralph.policy.validation import validate_policy_completeness
    return validate_policy_completeness

_LOADERS: dict[str, Callable[[], object]] = {
    "AgentChainConfig": _load_AgentChainConfig,
    "AgentDrainConfig": _load_AgentDrainConfig,
    "AgentsPolicy": _load_AgentsPolicy,
    "ArtifactContract": _load_ArtifactContract,
    "ArtifactsPolicy": _load_ArtifactsPolicy,
    "CheckpointPolicyMismatchError": _load_CheckpointPolicyMismatchError,
    "CommitReviewPolicy": _load_CommitReviewPolicy,
    "DrainName": _load_DrainName,
    "PhaseDefinition": _load_PhaseDefinition,
    "PhaseTransition": _load_PhaseTransition,
    "PipelinePolicy": _load_PipelinePolicy,
    "PolicyBundle": _load_PolicyBundle,
    "PolicyValidationError": _load_PolicyValidationError,
    "load_policy": _load_load_policy,
    "load_policy_or_die": _load_load_policy_or_die,
    "validate_chain_exists": _load_validate_chain_exists,
    "validate_checkpoint_compatible": _load_validate_checkpoint_compatible,
    "validate_drain_bound": _load_validate_drain_bound,
    "validate_phase_exists_in_policy": _load_validate_phase_exists_in_policy,
    "validate_policy_completeness": _load_validate_policy_completeness,
}


def __getattr__(name: str) -> object:
    loader = _LOADERS.get(name)
    if loader is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return loader()
