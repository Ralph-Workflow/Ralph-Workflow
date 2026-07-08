# Documentation Map

This page routes readers to the one maintained doc that matches their
question, after [`README.md`](../README.md) and
[`START_HERE.md`](../START_HERE.md).

## Fastest first successful run

- [Getting started](../ralph-workflow/docs/sphinx/getting-started.md)
- [Run diagnostics](../ralph-workflow/docs/sphinx/diagnostics.md)

## Configure

- [Configuration reference](../ralph-workflow/docs/sphinx/configuration.md)
- [CLI reference](../ralph-workflow/docs/sphinx/cli.md)
- [Advanced pipeline configuration](../ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md)
- [Advanced MCP configuration](../ralph-workflow/docs/sphinx/advanced-mcp-configuration.md)
- [Advanced artifact configuration](../ralph-workflow/docs/sphinx/advanced-artifact-configuration.md)

## Understand

- [Concepts](../ralph-workflow/docs/sphinx/concepts.md)
- [MCP architecture](../ralph-workflow/docs/sphinx/mcp-architecture.md)
- [Artifacts reference](../ralph-workflow/docs/sphinx/artifacts.md)
- [Recovery model](../ralph-workflow/docs/sphinx/recovery.md)

## Operate

- [Troubleshooting](../ralph-workflow/docs/sphinx/troubleshooting.md)
- [Agent compatibility](../ralph-workflow/docs/sphinx/agent-compatibility.md)
- [Agent CLI lifecycle](../ralph-workflow/docs/sphinx/agents.md)
- [MCP tools](../ralph-workflow/docs/sphinx/mcp-tools.md)
- [MCP tool restriction](../ralph-workflow/docs/sphinx/mcp-tool-restriction.md)
- [Versioning](../ralph-workflow/docs/sphinx/versioning.md)
- [Pro support](../ralph-workflow/docs/sphinx/pro-support.md)

## Develop

- [Developer internals](../ralph-workflow/docs/sphinx/developer-internals.md)
- [Adding a new agent](../ralph-workflow/docs/agents/adding-a-new-agent.md)

## Example apps

- [Example Flask API starter](../example-api/README.md) -- the canonical
  Flask `/health` starter used as the reference for a small first task;
  see the [proof page](examples/example-api.md) for the rubric-style
  interpretation.

## Contribute

- [`CONTRIBUTING.md`](../CONTRIBUTING.md)
- [`docs/agents/`](agents/README.md) — verification, testing,
  type-ignore, workspace-trait, agent-support architecture, and the
  fabrication guard
- [`docs/code-style/`](../code-style/index.md) — the maintained Python
  style guide (canonical home; the legacy `CODE_STYLE.md` was removed)

## Architecture

- [`docs/architecture/`](architecture/README.md) — repo-root
  architecture index (one-paragraph pointer into the package's
  runtime internals)
- [`ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md`](../ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md) —
  the MADR-format ADR for the interrupt architecture

## Retired Rust implementation

[`docs/legacy-rust/`](legacy-rust/README.md) is the quarantined pointer
to the retired Rust implementation. Do not act on it for current
behavior; the Python implementation is authoritative.