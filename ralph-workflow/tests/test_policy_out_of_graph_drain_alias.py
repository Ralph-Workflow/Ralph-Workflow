"""Out-of-graph drains must survive a workspace that ships its own agents.toml.

Three drains are bound to no ``[blocks.*]`` phase: ``policy_remediation``,
``policy_remediation_analysis`` and ``rebase_conflict_resolution``. Because
nothing in the pipeline graph references them, a user-supplied
``agents.toml`` that simply omits them produces NO error -- the drain just
fails to resolve, the feature behind it silently declines, and the
operator sees a capability quietly stop working.

That is exactly what happened to ``rebase_conflict_resolution``: it was
absent from the alias list in :mod:`ralph.policy.loader`, so in any
workspace with its own agents policy every rebase conflict fell straight
through to an abort. This test asserts the property for ALL out-of-graph
drains at once, so the next drain added cannot regress the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from pathlib import Path

#: Drains reachable only from code, never from a pipeline phase.
_OUT_OF_GRAPH_DRAINS = (
    "policy_remediation",
    "policy_remediation_analysis",
    "rebase_conflict_resolution",
)

#: A minimal, realistic user policy: it redefines the pipeline's own
#: chains and drains and says nothing at all about the out-of-graph ones.
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
    """A user policy that omits them must not leave any of them unbound."""
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
    """Redirecting development must redirect conflict resolution with it."""
    _write_user_policy(tmp_path)

    drain = load_policy(tmp_path).agents.agent_drains["rebase_conflict_resolution"]

    assert drain.chain == "development"


def test_the_rebase_drain_keeps_the_development_capability_class(
    tmp_path: Path,
) -> None:
    """The resolver must keep workspace.edit, or it cannot rewrite the files."""
    _write_user_policy(tmp_path)

    drain = load_policy(tmp_path).agents.agent_drains["rebase_conflict_resolution"]

    assert drain.drain_class == "development"


def test_a_users_own_rebase_chain_wins_over_the_alias(tmp_path: Path) -> None:
    """An explicit chain of the same name is honoured, not overridden."""
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
