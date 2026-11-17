from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _load_agentchainconfig() -> object:
    from ralph.policy.models import AgentChainConfig
    return AgentChainConfig

def _load_agentdrainconfig() -> object:
    from ralph.policy.models import AgentDrainConfig
    return AgentDrainConfig

def _load_agentspolicy() -> object:
    from ralph.policy.models import AgentsPolicy
    return AgentsPolicy

def _load_artifactcontract() -> object:
    from ralph.policy.models import ArtifactContract
    return ArtifactContract

def _load_artifactspolicy() -> object:
    from ralph.policy.models import ArtifactsPolicy
    return ArtifactsPolicy

def _load_checkpointpolicymismatcherror() -> object:
    from ralph.policy.validation import CheckpointPolicyMismatchError
    return CheckpointPolicyMismatchError

def _load_commitreviewpolicy() -> object:
    from ralph.policy.models import CommitReviewPolicy
    return CommitReviewPolicy

def _load_drainname() -> object:
    from ralph.policy.models import DrainName
    return DrainName

def _load_phasedefinition() -> object:
    from ralph.policy.models import PhaseDefinition
    return PhaseDefinition

def _load_phasetransition() -> object:
    from ralph.policy.models import PhaseTransition
    return PhaseTransition

def _load_pipelinepolicy() -> object:
    from ralph.policy.models import PipelinePolicy
    return PipelinePolicy

def _load_policybundle() -> object:
    from ralph.policy.models import PolicyBundle
    return PolicyBundle

def _load_policyvalidationerror() -> object:
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
    "AgentChainConfig": _load_agentchainconfig,
    "AgentDrainConfig": _load_agentdrainconfig,
    "AgentsPolicy": _load_agentspolicy,
    "ArtifactContract": _load_artifactcontract,
    "ArtifactsPolicy": _load_artifactspolicy,
    "CheckpointPolicyMismatchError": _load_checkpointpolicymismatcherror,
    "CommitReviewPolicy": _load_commitreviewpolicy,
    "DrainName": _load_drainname,
    "PhaseDefinition": _load_phasedefinition,
    "PhaseTransition": _load_phasetransition,
    "PipelinePolicy": _load_pipelinepolicy,
    "PolicyBundle": _load_policybundle,
    "PolicyValidationError": _load_policyvalidationerror,
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
