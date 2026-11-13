# Documentation Map

For what Ralph Workflow is and whether it fits, start with
[`README.md`](../README.md). Use this page when you already know the
question and need the one maintained doc that answers it. Each section is one
reader job; pick the first link that matches, then stop.

## I want a successful first run (tutorial)

- [`START_HERE.md`](../START_HERE.md) — shortest runnable path
  (install → init → list agents → diagnose → one task → run → review)
- [Getting started](../ralph-workflow/docs/sphinx/getting-started.md) —
  fuller tutorial with task picking and `PROMPT.md` templates
- [Run diagnostics](../ralph-workflow/docs/sphinx/diagnostics.md) —
  what `ralph --diagnose` checks and how to fix failures
- [Agent CLI lifecycle](../ralph-workflow/docs/sphinx/agents.md) —
  install, authenticate, and confirm the agent Ralph Workflow will use

## I need to configure Ralph Workflow (how-to, reference)

- [Configuration reference](../ralph-workflow/docs/sphinx/configuration.md) —
  `ralph-workflow.toml`, project-local overrides, Git sync, and
  auto-integration, plus the
  [bundled defaults](../ralph-workflow/docs/sphinx/configuration.md#bundled-defaults)
  that ship with the package
- [CLI reference](../ralph-workflow/docs/sphinx/cli.md) — flags and depth presets
- [Advanced pipeline configuration](../ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md) —
  `pipeline.toml` phases and routing
- [Advanced MCP configuration](../ralph-workflow/docs/sphinx/advanced-mcp-configuration.md) —
  `mcp.toml` tool surface
- [Advanced artifact configuration](../ralph-workflow/docs/sphinx/advanced-artifact-configuration.md) —
  `artifacts.toml` expectations

## I want to understand how it works (explanation, reference)

- [Concepts](../ralph-workflow/docs/sphinx/concepts.md) — glossary and how
  the plan → build → verify → fix loop, review, recovery, and Git
  integration fit into the workflow graph
- [MCP architecture](../ralph-workflow/docs/sphinx/mcp-architecture.md) —
  the tool surface each agent session runs against
- [Artifacts reference](../ralph-workflow/docs/sphinx/artifacts.md) —
  the output contract each phase has to satisfy
- [Recovery model](../ralph-workflow/docs/sphinx/recovery.md) — retries,
  checkpoints, and resuming an interrupted run

## I am operating a running setup (how-to, reference)

- [Troubleshooting](../ralph-workflow/docs/sphinx/troubleshooting.md) —
  what to do when a run fails or stalls
- [Agent compatibility](../ralph-workflow/docs/sphinx/agent-compatibility.md) —
  per-agent model strings and transport notes
- [MCP tools](../ralph-workflow/docs/sphinx/mcp-tools.md) —
  the tools an agent session can call
- [MCP tool restriction](../ralph-workflow/docs/sphinx/mcp-tool-restriction.md) —
  how the tool surface is narrowed per agent
- [Versioning](../ralph-workflow/docs/sphinx/versioning.md) —
  version policy and compatibility
- [Operator manual](../ralph-workflow/docs/sphinx/index.rst) — the whole
  manual in one table of contents

## I want a worked example / proof (proof)

- [Example Flask API starter](../example-api/README.md) — the canonical
  Flask `/health` starter for a small first task
- [Proof page](examples/example-api.md) — how to interpret that example

## I want to extend or develop Ralph Workflow (internals)

- [Developer internals](../ralph-workflow/docs/sphinx/developer-internals.md)
- [Adding a new agent](../ralph-workflow/docs/agents/adding-a-new-agent.md)

## I am contributing code or docs (internals)

- [`CONTRIBUTING.md`](../CONTRIBUTING.md)
- [`docs/agents/`](agents/README.md) — the agent subsystem: adding,
  updating, and removing an agent, and how it is structured
- [`docs/code-style/`](code-style/index.md) — the maintained Python
  style guide (`documentation-rubric.md` and `index.md`)

## I need architecture notes (internals)

- [`docs/architecture/`](architecture/README.md) — repo-root
  architecture index (one-paragraph pointer into the package's
  runtime internals)
- [`ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md`](../ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md) —
  the interrupt architecture ADR

## I am looking at the retired Rust implementation (legacy)

[`docs/legacy-rust/`](legacy-rust/README.md) is the quarantined pointer
to the retired Rust implementation. Do not act on it for current
behavior; the Python implementation is authoritative.
