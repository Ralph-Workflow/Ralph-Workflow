# Quickstart: Add a New Agent in 5 Lines

See also: [Agent Subsystem README](README.md) for the unified entry point.

## Goal

Register a new headless or interactive agent using the opinionated
5-line `register_my_agent` recipe. The helper picks the right
execution strategy from the transport and applies the default
`--resume {}` session template for interactive agents.

## Prerequisites

- `ralph-workflow` is installed (`make dev` from `ralph-workflow/`).
- The agent name is unique in your `AgentCatalog` (custom agents cannot
  reuse the six built-in parser keys: `claude`, `claude-headless`,
  `codex`, `opencode`, `nanocoder`, `agy`).

## Steps

### 1. Register a headless agent

<!-- BLACKBOX_RECIPE_START -->
```python
from ralph.agents import register_my_agent
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

my_registry = AgentRegistry()
register_my_agent(
    name="my-headless-agent",
    transport=AgentTransport.GENERIC,
    parser=GenericParser,
    agent_registry=my_registry,
)
```
<!-- BLACKBOX_RECIPE_END -->

The helper picks `GenericExecutionStrategy` from the transport, so the
recipe does not pass `strategy=`.

### 2. Register an interactive agent

<!-- BLACKBOX_RECIPE_START -->
```python
from ralph.agents import register_my_agent
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

my_registry = AgentRegistry()
register_my_agent(
    name="my-interactive-agent",
    transport=AgentTransport.CLAUDE_INTERACTIVE,
    parser=ClaudeParser,
    agent_registry=my_registry,
    interactive=True,
)
```
<!-- BLACKBOX_RECIPE_END -->

`interactive=True` auto-applies the `--resume {}` session template. Pass
`no_default_session_flag=True` to opt out (used by `agy`).

## Expected outcome

After either recipe runs, `my_registry.catalog.get(name)` returns an
`AgentSupport`, `get_parser(name)` returns a parser instance,
`get_strategy(transport, command=name)` returns a strategy, and
`build_command(config, "PROMPT.md", options=...)` returns an argv list
whose first element is the agent's `cmd`.

## Next steps

- 14-kwarg advanced form: [adding-a-new-agent.md](adding-a-new-agent.md)
- Architecture: [architecture.md](architecture.md)
- Update + Remove: [adding-a-new-agent.md](adding-a-new-agent.md#update-an-existing-agent),
  [adding-a-new-agent.md#remove-an-agent](adding-a-new-agent.md#remove-an-agent)
