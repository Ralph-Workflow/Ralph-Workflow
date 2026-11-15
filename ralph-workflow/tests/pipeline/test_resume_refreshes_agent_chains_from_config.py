from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.run_loop import resolve_initial_state
from ralph.pipeline.runner import load_policy_bundle_for_run
from ralph.workspace import WorkspaceScope

pytestmark = [pytest.mark.timeout_seconds(5)]

_CONFIGURED = ["opencode/minimax-coding-plan/MiniMax-M3", "codex/gpt-5.6-terra[effort=low]"]


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "agent_chains": {
                "planning": _CONFIGURED,
                "development": _CONFIGURED,
                "analysis": _CONFIGURED,
                "commit": _CONFIGURED,
            }
        }
    )


def _resume(tmp_path: Path, saved: dict[str, AgentChainState]) -> dict[str, AgentChainState]:
    config = _config()
    bundle = load_policy_bundle_for_run(WorkspaceScope(tmp_path), config)
    fresh = resolve_initial_state(
        config=config,
        policy_bundle=bundle,
        initial_state=None,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
    )
    resumed = resolve_initial_state(
        config=config,
        policy_bundle=bundle,
        initial_state=fresh.copy_with(phase_chains={**fresh.phase_chains, **saved}),
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
    )
    return resumed.phase_chains


def test_a_resumed_run_uses_the_configured_agents_not_the_checkpointed_ones(
    tmp_path: Path,
) -> None:
    chains = _resume(
        tmp_path, {"planning": AgentChainState(agents=["claude/opus"], current_index=0)}
    )

    assert chains["planning"].agents == _CONFIGURED
    assert all("claude/opus" not in chain.agents for chain in chains.values())


def test_a_resumed_run_keeps_its_chain_position_when_the_agents_are_unchanged(
    tmp_path: Path,
) -> None:
    chains = _resume(
        tmp_path, {"planning": AgentChainState(agents=_CONFIGURED, current_index=1, retries=2)}
    )

    assert chains["planning"] == AgentChainState(agents=_CONFIGURED, current_index=1, retries=2)


def test_configured_chains_replace_the_packaged_agents_even_without_configured_drains(
    tmp_path: Path,
) -> None:
    bundle = load_policy_bundle_for_run(WorkspaceScope(tmp_path), _config())

    assert bundle.agents.agent_chains["planning"].agents == _CONFIGURED
    assert all(
        not agent.startswith("claude")
        for chain in bundle.agents.agent_chains.values()
        for agent in chain.agents
    )
