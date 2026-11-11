from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from pathlib import Path

_OUT_OF_GRAPH_DRAINS = (
    "policy_remediation",
    "policy_remediation_analysis",
    "rebase_conflict_resolution",
)

_USER_AGENTS_TOML = """
[agent_chains.development]
agents = ["opencode"]
max_retries = 2
retry_delay_ms = 1000

[agent_chains.development_analysis]
agents = ["opencode"]
max_retries = 2
retry_delay_ms = 1000

[agent_drains.development]
chain = "development"
drain_class = "development"

[agent_drains.development_analysis]
chain = "development_analysis"
drain_class = "analysis"
"""


def _write_user_policy(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents.toml").write_text(_USER_AGENTS_TOML, encoding="utf-8")


def test_every_out_of_graph_drain_resolves_from_a_user_agents_toml(
    tmp_path: Path,
) -> None:
    _write_user_policy(tmp_path)

    drains = load_policy(tmp_path).agents.agent_drains

    missing = [name for name in _OUT_OF_GRAPH_DRAINS if name not in drains]
    assert not missing, (
        f"out-of-graph drains vanished under a user agents.toml: {missing}. "
        "Register them in _merge_agents_policy_onto_defaults via "
        "_alias_out_of_graph_drain, or the feature behind each one silently "
        "declines in every workspace with its own agents policy."
    )


def test_the_rebase_drain_follows_the_users_development_chain(
    tmp_path: Path,
) -> None:
    _write_user_policy(tmp_path)

    drain = load_policy(tmp_path).agents.agent_drains["rebase_conflict_resolution"]

    assert drain.chain == "development"


def test_the_rebase_drain_keeps_the_development_capability_class(
    tmp_path: Path,
) -> None:
    _write_user_policy(tmp_path)

    drain = load_policy(tmp_path).agents.agent_drains["rebase_conflict_resolution"]

    assert drain.drain_class == "development"


def test_a_users_own_rebase_chain_wins_over_the_alias(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "agents.toml").write_text(
        _USER_AGENTS_TOML
        + """
[agent_chains.rebase_conflict_resolution]
agents = ["claude"]
max_retries = 1
retry_delay_ms = 500
""",
        encoding="utf-8",
    )

    drain = load_policy(tmp_path).agents.agent_drains["rebase_conflict_resolution"]

    assert drain.chain == "rebase_conflict_resolution"
