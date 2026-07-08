# Agent Subsystem

> **Role:** This directory is the **agent-authoring contracts** home for the
> Ralph Workflow Python package. It is distinct from the repo-root
> [`docs/agents/`](../../../docs/agents/README.md), which carries contributor
> policy and verification guides. Cross-link, do not duplicate.

The agent subsystem in Ralph coordinates executing, composing, and registering AI agents. It provides abstractions to support interactive agents that run inside a PTY, headless agents running as subprocesses, and registries to manage their configurations and execution strategies.

## How do I...?

- [Add a new agent (5-minute quickstart)](quickstart-add-a-new-agent.md)
- [Add a new agent (advanced reference: Add, Update, Remove)](adding-a-new-agent.md)
- [Update an existing agent](adding-a-new-agent.md#update-an-existing-agent)
- [Remove an agent](adding-a-new-agent.md#remove-an-agent)
- [Understand the architecture](architecture.md)

## Source of Truth

The table below maps the primary public agent subsystem symbols to their declaring modules:

| Public Symbol | Target Module | Purpose |
|---|---|---|
| `register_agent_support` | `ralph.agents.registration` | Register custom agent configurations and factories. |
| `AgentSupport` | `ralph.agents.support` | Define structural settings and factories for a specific agent. |
| `AgentCatalog` | `ralph.agents.catalog` | Injectable catalog containing supported agent registrations. |
| `default_catalog` | `ralph.agents.catalog` | Global catalog instance used by `register_agent_support`. Built-in supports are seeded into it by `AgentRegistry.from_config()` and into an injected catalog by `AgentRegistry(catalog=...)` via `_seed_catalog_with_builtins`; it is not auto-seeded at module import. |
| `AgentRegistry` | `ralph.agents.registry` | Registry mapping agent names to configurations. |
| `AgentChain` | `ralph.agents.chain` | Chain multiple agents together for sequential workflows. |
| `invoke_agent` | `ralph.agents.invoke` | Invoke a registered agent and parse its streaming NDJSON output. |

## Contracts in this tree

- `adding-a-new-agent.md` / `quickstart-add-a-new-agent.md` — how to register
  a new agent CLI (Build/Update/Remove contract)
- `architecture.md` — subsystem shape and runtime responsibilities
- `artifact-submission-contract.md` — what every submitted artifact must
  contain (referenced as the authoritative contract by `AGENTS.md`)
- `memory-lifecycle.md` — bounded-accumulator rules for agent-owned
  collections (complements `ralph/testing/audit_resource_lifecycle.py`)
- Pro support layer contract lives at
  [`ralph-workflow/docs/sphinx/pro-support.md#engine-internals-pro-contract`](../sphinx/pro-support.md#engine-internals-pro-contract)
- `watchdog-spec.md` — watchdog design and invariants

## Cross-reference

Repo-root contributor policy that interacts with these contracts lives in
[`docs/agents/`](../../../docs/agents/README.md):

- `verification.md` — the `make verify` workflow and audit allowlists
- `testing-guide.md` — black-box test expectations
- `workspace-trait.md` — workspace abstraction contract
- `agent-support-architecture.md` — how this repo's own runtime supports
  Ralph Workflow agents