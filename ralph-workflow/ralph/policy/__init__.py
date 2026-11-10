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

Public names resolve lazily, on first attribute access, from the module that
defines them. Importing a leaf submodule such as ``ralph.policy.models`` no
longer pulls the loader (and through it ``ralph.config`` and the MCP stack)
into the importing process, which keeps every interpreter that touches the
policy models cheap to start.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

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
    # Models (alphabetical - uppercase before lowercase per ASCII)
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
    # Validation (alphabetical)
    "validate_chain_exists",
    "validate_checkpoint_compatible",
    "validate_drain_bound",
    "validate_phase_exists_in_policy",
    "validate_policy_completeness",
]

#: Public name -> (defining module, attribute name in that module).
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentChainConfig": ("ralph.policy.models", "AgentChainConfig"),
    "AgentDrainConfig": ("ralph.policy.models", "AgentDrainConfig"),
    "AgentsPolicy": ("ralph.policy.models", "AgentsPolicy"),
    "ArtifactContract": ("ralph.policy.models", "ArtifactContract"),
    "ArtifactsPolicy": ("ralph.policy.models", "ArtifactsPolicy"),
    "CheckpointPolicyMismatchError": (
        "ralph.policy.validation",
        "CheckpointPolicyMismatchError",
    ),
    "CommitReviewPolicy": ("ralph.policy.models", "CommitReviewPolicy"),
    "DrainName": ("ralph.policy.models", "DrainName"),
    "PhaseDefinition": ("ralph.policy.models", "PhaseDefinition"),
    "PhaseTransition": ("ralph.policy.models", "PhaseTransition"),
    "PipelinePolicy": ("ralph.policy.models", "PipelinePolicy"),
    "PolicyBundle": ("ralph.policy.models", "PolicyBundle"),
    "PolicyValidationError": ("ralph.policy.validation", "PolicyValidationError"),
    "load_policy": ("ralph.policy.loader", "load_policy"),
    "load_policy_or_die": ("ralph.policy.loader", "load_policy_or_die"),
    "validate_chain_exists": ("ralph.policy.validation", "validate_chain_exists"),
    "validate_checkpoint_compatible": (
        "ralph.policy.validation",
        "validate_checkpoint_compatible",
    ),
    "validate_drain_bound": ("ralph.policy.validation", "validate_drain_bound"),
    "validate_phase_exists_in_policy": (
        "ralph.policy.validation",
        "validate_phase_exists_in_policy",
    ),
    "validate_policy_completeness": (
        "ralph.policy.validation",
        "validate_policy_completeness",
    ),
}


def __getattr__(name: str) -> object:
    """Resolve a public export from its defining module on first access.

    The eager imports this replaces pulled ``ralph.policy.loader`` -- and
    through it ``ralph.config`` and the MCP startup stack -- into every
    process that only needed the policy models.
    """
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from exc

    module = import_module(module_name)
    namespace = cast("dict[str, object]", module.__dict__)
    value = namespace[attr_name]
    cast("dict[str, object]", globals())[name] = value
    return value
