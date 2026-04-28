"""Policy module for Ralph orchestration configuration.

This module provides the policy layer that drives Ralph's configurable
orchestration. Policy is expressed in three TOML files:

- agents.toml: Agent chains and drain-to-chain bindings
- pipeline.toml: Phase graph and transition routing
- artifacts.toml: Artifact contracts per drain

The loader validates all three files together, ensuring cross-file consistency
(e.g., every drain used in pipeline.toml is bound in agents.toml).

Example usage::

    from pathlib import Path
    from ralph.policy import load_policy

    bundle = load_policy(Path(".agent"))
    drain_binding = bundle.agents.agent_drains["planning"]
    chain = bundle.agents.agent_chains[drain_binding.chain]
"""

from ralph.policy.loader import (
    PolicyValidationError,
    load_policy,
    load_policy_or_die,
)
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    DrainName,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
    validate_chain_exists,
    validate_checkpoint_compatible,
    validate_drain_bound,
    validate_phase_exists_in_policy,
    validate_policy_completeness,
)

__all__ = [
    # Models (alphabetical - uppercase before lowercase per ASCII)
    "AgentChainConfig",
    "AgentDrainConfig",
    "AgentsPolicy",
    "ArtifactContract",
    "ArtifactsPolicy",
    "CheckpointPolicyMismatchError",
    "DrainName",
    "PhaseDefinition",
    "PhaseTransition",
    "PipelinePolicy",
    "PolicyBundle",
    "PolicyValidationError",
    "load_policy",
    "load_policy_or_die",
    # Validation (alphabetical)
    "validate_chain_exists",
    "validate_checkpoint_compatible",
    "validate_drain_bound",
    "validate_phase_exists_in_policy",
    "validate_policy_completeness",
]
