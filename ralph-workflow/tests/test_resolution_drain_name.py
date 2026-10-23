"""The integration-resolution drain has one name, and the shipped policy declares it."""

from __future__ import annotations

from pathlib import Path

from ralph.policy.loader import load_policy
from ralph.policy.resolution_names import RESOLUTION_DRAIN_NAME

_DEFAULTS = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def test_shipped_policy_declares_a_chain_for_the_resolution_drain() -> None:
    bundle = load_policy(_DEFAULTS)

    binding = bundle.agents.agent_drains[RESOLUTION_DRAIN_NAME]

    assert bundle.agents.agent_chains[binding.chain].agents


def test_resolution_drain_name_is_the_documented_policy_key() -> None:
    assert RESOLUTION_DRAIN_NAME == "rebase_conflict_resolution"


def test_resolver_chain_follows_the_users_development_chain_when_undeclared(
    tmp_path: Path,
) -> None:
    (tmp_path / "agents.toml").write_text(
        '[agent_chains.development]\nagents = ["mine/a", "mine/b"]\n'
        '[agent_drains.development]\nchain = "development"\ndrain_class = "development"\n',
        encoding="utf-8",
    )
    from ralph.policy.loader import load_agents_policy

    policy = load_agents_policy(tmp_path)

    binding = policy.agent_drains[RESOLUTION_DRAIN_NAME]
    assert policy.agent_chains[binding.chain].agents == ["mine/a", "mine/b"]
