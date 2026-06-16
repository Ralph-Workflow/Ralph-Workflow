# Agent Subsystem

The agent subsystem in Ralph coordinates executing, composing, and registering AI agents. It provides abstractions to support interactive agents that run inside a PTY, headless agents running as subprocesses, and registries to manage their configurations and execution strategies.

## How do I...?

- [Add a new agent](adding-a-new-agent.md)
- [Understand the architecture](architecture.md)
- [Follow the timeout policy](timeout-policy.md)

## Source of Truth

The table below maps the primary public agent subsystem symbols to their declaring modules:

| Public Symbol | Target Module | Purpose |
|---|---|---|
| `register_agent_support` | `ralph.agents.registration` | Register custom agent configurations and factories. |
| `AgentSupport` | `ralph.agents.support` | Define structural settings and factories for a specific agent. |
| `AgentCatalog` | `ralph.agents.catalog` | Injectable catalog containing supported agent registrations. |
| `default_catalog` | `ralph.agents.catalog` | Global catalog instance seeded with default built-in supports. |
| `AgentRegistry` | `ralph.agents.registry` | Registry mapping agent names to configurations. |
| `AgentChain` | `ralph.agents.chain` | Chain multiple agents together for sequential workflows. |
| `invoke_agent` | `ralph.agents.invoke` | Invoke a registered agent and parse its streaming NDJSON output. |
