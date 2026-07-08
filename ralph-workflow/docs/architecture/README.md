# Architecture Docs (package)

This directory holds Architecture Decision Records (ADRs) and
architecture-level notes for the **maintained Python package** at
`ralph-workflow/`.

## Who this is for

Contributors and reviewers who want the agreed shape of an
architectural decision before they read the code that implements it.

## Read this first

- **ADR:** [adr-0001-interrupt-architecture.md](adr-0001-interrupt-architecture.md) — the maintained ADR for the package's interrupt architecture.
- **Runtime overview:** [Repo-root architecture overview](../../../docs/architecture/README.md), then [Pipeline lifecycle](../../docs/sphinx/developer-internals.md#pipeline-lifecycle-high-level), [Event loop and reducers](../../docs/sphinx/developer-internals.md#event-loop-and-reducers), and [Parallel Mode (agent-driven)](../sphinx/advanced-pipeline-configuration.md#parallel-execution-agent-driven).
- **Sphinx operator manual:** [index.rst](../../docs/sphinx/index.rst), [Developer internals](../../docs/sphinx/developer-internals.md).

## Primary repo

- Codeberg (primary): <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- GitHub (read-only mirror): <https://github.com/Ralph-Workflow/Ralph-Workflow>