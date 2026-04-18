from __future__ import annotations

from pathlib import Path as _RuntimePath

import pytest

import ralph.config.models as _config_models
from ralph.agents.chain import (
    AgentChain,
    ChainManager,
    DrainNotBoundError,
    create_chain_from_config,
)
from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy

_config_models.Path = _RuntimePath
_config_models.GeneralConfig.model_rebuild()
_config_models.UnifiedConfig.model_rebuild()


def test_agent_chain_retries_and_fallback() -> None:
    chain = AgentChain(agents=["alpha", "beta"], max_retries=1)

    assert chain.current_agent == "alpha"
    assert chain.can_retry()

    chain.record_retry()
    assert not chain.can_retry()

    assert chain.advance()
    assert chain.current_agent == "beta"
    assert chain.retries == 0

    chain.record_retry()
    assert not chain.can_retry()
    assert not chain.advance()
    assert chain.current_agent == "beta"


def test_agent_chain_backoff_caps_at_max() -> None:
    chain = AgentChain(
        agents=["solo"],
        retry_delay_ms=100,
        backoff_multiplier=10.0,
        max_backoff_ms=150,
    )

    chain.record_retry()
    chain.record_retry()
    chain.record_retry()

    assert chain.calculate_backoff() == pytest.approx(0.15)


def test_chain_manager_resolves_bound_chain() -> None:
    policy = AgentsPolicy(
        agent_chains={"primary": AgentChainConfig(agents=["claude"])},
        agent_drains={"planning": AgentDrainConfig(chain="primary")},
    )
    manager = ChainManager(policy)

    config = manager.chain_for_drain("planning")
    assert config.agents == ["claude"]
    assert manager.validate() == []


def test_chain_manager_missing_drain_raises() -> None:
    policy = AgentsPolicy(
        agent_chains={"primary": AgentChainConfig(agents=["claude"])},
        agent_drains={"planning": AgentDrainConfig(chain="primary")},
    )
    manager = ChainManager(policy)

    with pytest.raises(DrainNotBoundError) as exc:
        manager.chain_for_drain("development")

    assert exc.value.available_drains == {"planning"}


def test_chain_manager_validate_reports_unknown_chain_and_empty_agents() -> None:
    policy = AgentsPolicy.model_construct(
        agent_chains={"empty": AgentChainConfig.model_construct(agents=[])},
        agent_drains={"planning": AgentDrainConfig.model_construct(chain="missing")},
    )
    manager = ChainManager(policy)

    assert manager.validate() == [
        "Drain 'planning' references unknown chain 'missing'",
        "Chain 'empty' has no agents",
    ]


def test_create_chain_from_config_builds_chain() -> None:
    general = GeneralConfig(
        max_retries=7,
        retry_delay_ms=333,
        backoff_multiplier=1.5,
        max_backoff_ms=7777,
    )
    config = UnifiedConfig(general=general, agent_chains={"dev": ["alpha"]})

    chain = create_chain_from_config(config, "dev")
    assert chain is not None
    assert chain.max_retries == general.max_retries
    assert chain.retry_delay_ms == general.retry_delay_ms
    assert chain.current_agent == "alpha"


def test_create_chain_from_config_returns_none_when_missing() -> None:
    config = UnifiedConfig()
    assert create_chain_from_config(config, "ghost") is None


def test_agent_chain_is_exhausted_when_empty() -> None:
    chain = AgentChain(agents=[])

    assert chain.current_agent is None
    assert chain.is_exhausted


def test_agent_chain_waits_on_backoff(monkeypatch) -> None:
    chain = AgentChain(
        agents=["solo"],
        retry_delay_ms=200,
        backoff_multiplier=3.0,
        max_backoff_ms=1000,
    )
    chain.record_retry()
    chain.record_retry()

    called: list[float] = []

    def fake_sleep(duration: float) -> None:
        called.append(duration)

    monkeypatch.setattr("ralph.agents.chain.time.sleep", fake_sleep)
    chain.wait_backoff()

    assert called == [chain.calculate_backoff()]


def test_chain_manager_chain_for_drain_missing_chain_raises_value_error() -> None:
    policy = AgentsPolicy.model_construct(
        agent_chains={},
        agent_drains={
            "planning": AgentDrainConfig.model_construct(chain="missing"),
        },
    )
    manager = ChainManager(policy)

    with pytest.raises(ValueError) as excinfo:
        manager.chain_for_drain("planning")

    assert "Drain 'planning' references chain 'missing'" in str(excinfo.value)


def test_chain_manager_from_config_converts_legacy_policy() -> None:
    general = GeneralConfig(max_retries=2, retry_delay_ms=99)
    config = UnifiedConfig(
        general=general,
        agent_chains={"planner": ["claude"]},
        agent_drains={"planning": "planner"},
    )

    manager = ChainManager.from_config(config)

    chain = manager.chain_for_drain("planning")
    assert chain.agents == ["claude"]
    assert manager.validate() == []
