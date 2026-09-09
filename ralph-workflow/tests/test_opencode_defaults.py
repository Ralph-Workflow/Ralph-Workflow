"""Public default-routing contract for the OpenCode smoke command.

S-2 previously pinned the OpenCode smoke default to the literal
``opencode/minimax/MiniMax-M3`` in two places -- the Typer option in
``ralph/cli/main.py`` and the command function's own signature. That pinned a
provider/model the operator's pipeline never runs, and went stale the moment
the provider retired the id (exactly what happened to the Codex smoke's
``gpt-5-flash``). The default is now resolved from the operator's own
``[agent_chains]``, so the smoke exercises what the pipeline exercises.
"""

from __future__ import annotations

import inspect

from ralph.agents.registry import AgentRegistry
from ralph.cli import main
from ralph.cli.commands.smoke import smoke_interactive_opencode_command
from ralph.cli.commands.smoke_agent_defaults import resolve_default_smoke_agent
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy


def test_opencode_smoke_default_is_not_pinned_in_either_seam() -> None:
    """S-2: neither the CLI nor the command seam hardcodes a provider/model."""
    cli_default = inspect.signature(main.smoke_interactive_opencode).parameters["agent"].default
    command_default = (
        inspect.signature(smoke_interactive_opencode_command).parameters["agent_name"].default
    )

    assert cli_default.default is None
    assert command_default is None


def test_opencode_smoke_default_comes_from_the_development_policy_chain() -> None:
    config = UnifiedConfig(
        agent_chains={"development": ["opencode/config-only/stale"]}
    )
    agents_policy = AgentsPolicy(
        agent_chains={"smoke-development": AgentChainConfig(agents=["opencode/minimax/MiniMax-M3"])},
        agent_drains={"development": AgentDrainConfig(chain="smoke-development")},
    )
    lookup = AgentRegistry.from_config(config).get

    resolved = resolve_default_smoke_agent(
        AgentTransport.OPENCODE,
        agents_policy,
        lookup,
        drain="development",
    )

    assert resolved == "opencode/minimax/MiniMax-M3"


def test_opencode_smoke_default_rejects_development_chain_without_opencode_agent() -> None:
    config = UnifiedConfig()
    agents_policy = AgentsPolicy(
        agent_chains={"smoke-development": AgentChainConfig(agents=["claude/sonnet"])},
        agent_drains={"development": AgentDrainConfig(chain="smoke-development")},
    )
    lookup = AgentRegistry.from_config(config).get

    assert (
        resolve_default_smoke_agent(
            AgentTransport.OPENCODE,
            agents_policy,
            lookup,
            drain="development",
        )
        is None
    )


def test_opencode_smoke_default_requires_development_policy_binding() -> None:
    config = UnifiedConfig()
    agents_policy = AgentsPolicy(
        agent_chains={"unbound": AgentChainConfig(agents=["opencode/minimax/MiniMax-M3"])},
    )
    lookup = AgentRegistry.from_config(config).get

    assert (
        resolve_default_smoke_agent(
            AgentTransport.OPENCODE,
            agents_policy,
            lookup,
            drain="development",
        )
        is None
    )
