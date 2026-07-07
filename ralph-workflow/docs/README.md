# Documentation Map (package-side router)

This page is the maintained operator manual home for the ralph-workflow package.
through composition. **Hand it a well-specified coding task, let the
agents plan, build, verify, and fix, and come back to reviewable, tested
work.** The default workflow is already strong enough to adopt as-is
before you customize anything.

> **Codeberg is the primary repo.** Star, watch, and report issues there:
> <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror.

This page is the **package-side documentation router**. It groups every doc
under `ralph-workflow/docs/` by reader intent and points at the maintained
operator manual where it lives.

Use this page after [`../README.md`](../README.md) (public storefront) and
[`../START_HERE.md`](../START_HERE.md) (fastest first run).

Every route bullet below is tagged with its doc-family role
(`tutorial` / `how-to` / `reference` / `explanation` / `proof` /
`internals`) so you can match a route to the kind of page you need.

## Where each kind of doc lives

| Doc family                | Path                                                | Purpose                                                         |
| ------------------------- | --------------------------------------------------- | --------------------------------------------------------------- |
| Operator manual           | `sphinx/`                                           | Maintained tutorial / how-to / reference / explanation          |
| Agent-authoring contracts | `agents/`                                           | Adding or modifying the agent subsystem (Python package)        |
| Architecture              | `architecture/`                                     | ADRs and architectural decision records                         |
| MCP docs                  | `mcp/`                                              | MCP-specific reference and cookbook                             |
| Plans                     | `plans/`                                            | Python-era design and implementation plans                      |
| Skill-related             | `superpowers/`                                      | Skill system notes (not part of the operator route)             |

## Route by intent

### I'm an operator (running Ralph Workflow) — tutorial + how-to + reference

Start with the maintained manual:

- **[Manual home](sphinx/index.rst)** — how-to + reference
- [Getting Started](sphinx/getting-started.md) — tutorial
- [Diagnostics](sphinx/diagnostics.md) — how-to
- [Agent CLI lifecycle](sphinx/agents.md) — how-to + reference
- [Configuration](sphinx/configuration.md) — reference
- [CLI reference](sphinx/cli.md) — reference
- [Troubleshooting](sphinx/troubleshooting.md) — how-to

### I'm a contributor (changing the Python package) — how-to + internals

- [`agents/README.md`](agents/README.md) — internals (agent-authoring contracts)
- [`architecture/adr-0001-interrupt-architecture.md`](architecture/adr-0001-interrupt-architecture.md) —
  internals (current ADR)
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — how-to (top-level contribution guide)
- [Sphinx developer reference](sphinx/developer-reference.md) — reference
- [Sphinx developer internals](sphinx/developer-internals.md) — internals

### I'm comparing Ralph Workflow to other tools — explanation

- [Manual comparisons index](sphinx/index.rst) — explanation (every
  Ralph-vs-other page in the maintained manual; see the *Comparisons*
  section)

### I'm debugging an MCP-specific issue — how-to + reference

- [`mcp/` directory](mcp/) — reference (MCP tool restriction, transport notes,
  and cookbook pages)
- [Advanced MCP configuration](sphinx/advanced-mcp-configuration.md) — how-to

### I'm investigating an architectural decision — internals + explanation

- [`architecture/`](architecture/) — internals (ADRs)
- [`plans/`](plans/) — internals (Python-era plans)

### I want to understand the system boundary — explanation

- [Manual `concepts.md`](sphinx/concepts.md) — explanation (terminology)
- [Manual `ralph-loop.md`](sphinx/ralph-loop.md) — explanation (Ralph-loop mental model)
- [Manual `policy-driven-pipeline.md`](sphinx/policy-driven-pipeline.md) —
  explanation (policy interpretation)
- [Manual `phase-routing.md`](sphinx/phase-routing.md) — explanation (phase lifecycle)
- [Manual `artifact-lifecycle.md`](sphinx/artifact-lifecycle.md) — explanation (artifact submission flow)
- [Manual `watchdogs-and-timeouts.md`](sphinx/watchdogs-and-timeouts.md) —
  explanation (watchdog model)
- [Manual `verification-model.md`](sphinx/verification-model.md) — explanation (what each
  verification step proves)

## Cross-tree role split

There are two `docs/agents/` trees in the repo, each with a distinct role:

- **Repo-root `docs/agents/`** — contributor policy and verification guides
  (see [`../../docs/agents/README.md`](../../docs/agents/README.md))
- **This `ralph-workflow/docs/agents/`** — agent-authoring contracts for the
  Python package (see [`agents/README.md`](agents/README.md))

Cross-link, do not duplicate.

## Legacy and quarantined material

Material describing the **retired Rust implementation** lives at the repo
root under [`../../docs/legacy-rust/`](../../docs/legacy-rust/README.md).
Treat it as historical context only; do not act on it without confirming
against the maintained Python runtime.

## Primary repo

- Codeberg (primary): <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- GitHub (read-only mirror): <https://github.com/Ralph-Workflow/Ralph-Workflow>