# Documentation Map (package-side router)

> **Codeberg is the primary repo.** Star, watch, and report issues there:
> <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror.

This page is the **package-side documentation router**. It groups every doc
under `ralph-workflow/docs/` by reader intent and points at the maintained
operator manual where it lives.

Use this page after [`../README.md`](../README.md) (public storefront) and
[`../START_HERE.md`](../START_HERE.md) (fastest first run).

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

### I'm an operator (running Ralph Workflow)

Start with the maintained manual:

- **[Manual home](sphinx/index.rst)** — entry point for the operator manual
- [Getting Started](sphinx/getting-started.md) — first-run walkthrough
- [Diagnostics](sphinx/diagnostics.md) — pre-flight checks
- [Agent CLI lifecycle](sphinx/agents.md) — selection, auth, invocation
- [Configuration](sphinx/configuration.md) — config files and precedence
- [CLI reference](sphinx/cli.md) — every flag
- [Troubleshooting](sphinx/troubleshooting.md)

### I'm a contributor (changing the Python package)

- [`agents/README.md`](agents/README.md) — agent-authoring contracts
- [`architecture/adr-0001-interrupt-architecture.md`](architecture/adr-0001-interrupt-architecture.md) —
  current ADR
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — top-level contribution guide
- [Sphinx developer reference](sphinx/developer-reference.md) — maintained
  contributor reference
- [Sphinx developer internals](sphinx/developer-internals.md) — internal
  contracts and patterns

### I'm comparing Ralph Workflow to other tools

- [Manual comparisons index](sphinx/index.rst) — every
  Ralph-vs-other page in the maintained manual (see the *Comparisons*
  section)

### I'm debugging an MCP-specific issue

- [`mcp/` directory](mcp/) — MCP tool restriction, transport notes, and
  cookbook pages
- [Advanced MCP configuration](sphinx/advanced-mcp-configuration.md) — main
  manual page

### I'm investigating an architectural decision

- [`architecture/`](architecture/) — ADRs
- [`plans/`](plans/) — Python-era plans

### I want to understand the system boundary

- [Manual `concepts.md`](sphinx/concepts.md) — terminology
- [Manual `ralph-loop.md`](sphinx/ralph-loop.md) — the Ralph-loop mental
  model
- [Manual `policy-driven-pipeline.md`](sphinx/policy-driven-pipeline.md) —
  policy interpretation
- [Manual `phase-routing.md`](sphinx/phase-routing.md) — phase lifecycle
- [Manual `artifact-lifecycle.md`](sphinx/artifact-lifecycle.md) — artifact
  submission flow
- [Manual `watchdogs-and-timeouts.md`](sphinx/watchdogs-and-timeouts.md) —
  watchdog model
- [Manual `verification-model.md`](sphinx/verification-model.md) — what each
  verification step proves

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