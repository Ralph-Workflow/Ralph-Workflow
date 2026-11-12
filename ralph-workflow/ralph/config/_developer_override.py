from __future__ import annotations

from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.policy.validation import PolicyValidationError

_DEVELOPMENT_DRAIN = "development"
_DEVELOPMENT_CLASS = "development"
_CLI_CHAIN_NAME = "cli_developer"


def _agent_spec(agent: str | None, model: str | None, current: AgentChainConfig) -> str:
    if agent is not None and not agent.strip():
        raise PolicyValidationError("--developer-agent must name an agent", source="agents")
    if model is not None and not model.strip():
        raise PolicyValidationError("--developer-model must name a model", source="agents")
    base = agent.strip() if agent is not None else current.agents[0]
    if model is None:
        return base
    return f"{base.split('/', 1)[0]}/{model.strip()}"


def _pinned_value(config: object, name: str) -> str | None:
    value: object = getattr(config, name, None)
    return value if isinstance(value, str) else None


def apply_developer_override(policy: AgentsPolicy, config: object) -> AgentsPolicy:
    agent = _pinned_value(config, "developer_agent")
    model = _pinned_value(config, "developer_model")
    if agent is None and model is None:
        return policy
    binding = policy.agent_drains.get(_DEVELOPMENT_DRAIN)
    current = policy.agent_chains.get(binding.chain) if binding is not None else None
    if binding is None or current is None:
        raise PolicyValidationError(
            "--developer-agent/--developer-model need a bound `development` drain; "
            'add `development = "<chain>"` under [agent_drains]',
            source="agents",
        )
    pinned = AgentChainConfig(
        agents=[_agent_spec(agent, model, current)],
        max_retries=current.max_retries,
        retry_delay_ms=current.retry_delay_ms,
    )
    chain_name = _CLI_CHAIN_NAME
    while chain_name in policy.agent_chains:
        chain_name = f"_{chain_name}"
    drains = dict(policy.agent_drains)
    for drain, drain_binding in policy.agent_drains.items():
        follows_developer = drain_binding.chain == binding.chain and (
            drain_binding.drain_class == _DEVELOPMENT_CLASS
        )
        if drain == _DEVELOPMENT_DRAIN or follows_developer:
            drains[drain] = AgentDrainConfig(
                chain=chain_name,
                drain_class=drain_binding.drain_class,
                capability_class=drain_binding.capability_class,
            )
    return AgentsPolicy(
        agent_chains={**policy.agent_chains, chain_name: pinned},
        agent_drains=drains,
        forbid_sibling_drain_inference=policy.forbid_sibling_drain_inference,
    )
