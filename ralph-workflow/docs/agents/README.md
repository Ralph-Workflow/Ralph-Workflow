# Agent Subsystem

> **Role:** This directory is the **agent-authoring contracts** home for
> the Ralph Workflow Python package. It is distinct from the repo-root
> [`docs/agents/`](../../../docs/agents/README.md), which carries
> contributor policy. Cross-link, do not duplicate.

The agent subsystem coordinates executing, composing, and registering AI
agents. It supports interactive PTY agents, headless subprocess agents,
and registries to manage configurations and execution strategies.

## How do I...?

- [Add a new agent (5-min)](quickstart-add-a-new-agent.md)
- [Add a new agent](adding-a-new-agent.md)
- [Update an existing agent](adding-a-new-agent.md#update-an-existing-agent)
- [Remove an agent](adding-a-new-agent.md#remove-an-agent)
- [Architecture](architecture.md)

## Source of Truth

| Public Symbol            | Module                     | Purpose                                              |
| ------------------------ | -------------------------- | ---------------------------------------------------- |
| `register_agent_support` | `ralph.agents.registration`| Register custom agent configurations and factories.  |
| `AgentSupport`           | `ralph.agents.support`     | Settings + factories for a specific agent.           |
| `AgentCatalog`           | `ralph.agents.catalog`     | Injectable catalog for agent registrations.           |
| `AgentRegistry`          | `ralph.agents.registry`    | Maps agent names to configurations.                  |
| `AgentChain`             | `ralph.agents.chain`       | Sequential agent workflows.                          |
| `invoke_agent`           | `ralph.agents.invoke`      | Invoke a registered agent and parse NDJSON output.   |

## Contracts

- `adding-a-new-agent.md` / `quickstart-add-a-new-agent.md` — register a new agent CLI
- `architecture.md` — subsystem shape and runtime responsibilities
- `artifact-submission-contract.md` — required artifact content
- `memory-lifecycle.md` — bounded-accumulator rules
- Pro support: see
  [`docs/sphinx/pro-support.md#engine-internals-pro-contract`](../sphinx/pro-support.md#engine-internals-pro-contract)
- `watchdog-spec.md` — watchdog design and invariants
