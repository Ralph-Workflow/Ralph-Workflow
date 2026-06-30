# Architecture Docs (package)

This directory holds Architecture Decision Records (ADRs) and
architecture-level notes for the **maintained Python package** at
`ralph-workflow/`.

## Who this is for

Contributors and reviewers who want the agreed shape of an
architectural decision before they read the code that implements it.

## Read this first

- **[adr-0001-interrupt-architecture.md](adr-0001-interrupt-architecture.md)** —
  the maintained ADR for the package's interrupt architecture.

## Next click

For end-to-end Python-runtime architecture (Ralph loop, policy
interpretation, phase routing, agent invocation, artifact submission,
completion detection, verification, recovery, watchdogs, configuration,
and extension points), see the repo-root architecture overview:

- [Repo-root architecture overview](../../../docs/architecture/README.md)
- [Pipeline lifecycle](../../../docs/architecture/pipeline-lifecycle.md)
- [Event loop and reducers](../../../docs/architecture/event-loop-and-reducers.md)
- [Parallel fan-out](../../../docs/architecture/parallel-fan-out.md)

For the Sphinx operator manual, see:

- [Maintained operator manual](../../docs/sphinx/index.rst)
- [Developer reference](../../docs/sphinx/developer-reference.md)
- [Developer internals](../../docs/sphinx/developer-internals.md)

## Primary repo

- Codeberg (primary):
  <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- GitHub (read-only mirror):
  <https://github.com/Ralph-Workflow/Ralph-Workflow>
