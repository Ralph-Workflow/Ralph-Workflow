# Architecture Docs

This page is the architecture-doc index for readers who need to understand Ralph Workflow's system boundaries before editing code.
The simple core composes into a stronger workflow system for serious repo
work, and the default workflow is strong enough to start with before you
customize anything.

This directory holds the **current Python-runtime architecture documentation**.
For archived Rust-era design material, see
[`../legacy-rust/README.md`](../legacy-rust/README.md).

## Read this first

- **[overview.md](overview.md)** — end-to-end Python-runtime architecture
  overview: subsystem boundaries, ownership, data flow, and invariants
  for every layer from the CLI through the MCP server.

## Maintained: Current Python Behavior

These pages are kept current with the Python implementation in
`ralph-workflow/ralph/`:

- **[overview.md](overview.md)** — End-to-end Python-runtime architecture:
  Ralph loop, policy interpretation, phase routing, agent invocation,
  artifact submission, completion detection, verification, recovery,
  watchdogs, configuration, and extension points.
- **[pipeline-lifecycle.md](pipeline-lifecycle.md)** — End-to-end pipeline
  lifecycle: planning, development, commit, review, and fix loops.
  Policy-driven orchestration via `ralph/pipeline/`.
- **[event-loop-and-reducers.md](event-loop-and-reducers.md)** — Event loop,
  reducer architecture, and policy-based routing. Covers
  `ralph/pipeline/orchestrator.py` and `ralph/pipeline/reducer.py`.
- **[parallel-mode.md (see ralph-workflow/docs/sphinx/parallel-mode.md)](parallel-mode.md (see ralph-workflow/docs/sphinx/parallel-mode.md))** — Same-workspace v1 parallel
  fan-out. Key constraints: `allowed_directories` path isolation,
  `.agent/workers/<unit_id>/` namespaces, artifact-based worker completion.
  No per-worker git branches or post-development merge step. The bundled
  default ships with `dispatch_mode = "agent_subagents"`; see
  `ralph-workflow/docs/sphinx/parallel-mode.md` for the full opt-in
  contract.

## Quarantined: Rust-era material

All Rust-era architecture material is quarantined under
[`../legacy-rust/architecture/`](../legacy-rust/architecture/) with a clear
header noting it is unmaintained pre-Python-rewrite reference. Do not rely
on those pages for current behavior.

## Related

- [`../../ralph-workflow/docs/sphinx/`](../../ralph-workflow/docs/sphinx/index.rst) — the maintained Sphinx manual, including
  the concept pages ([`ralph-loop`](../../ralph-workflow/docs/sphinx/ralph-loop.md),
  [`policy-driven-pipeline`](../../ralph-workflow/docs/sphinx/policy-driven-pipeline.md),
  [`phase-routing`](../../ralph-workflow/docs/sphinx/phase-routing.md),
  [`artifact-lifecycle`](../../ralph-workflow/docs/sphinx/artifact-lifecycle.md),
  [`watchdogs-and-timeouts`](../../ralph-workflow/docs/sphinx/watchdogs-and-timeouts.md),
  [`verification-model`](../../ralph-workflow/docs/sphinx/verification-model.md))
- [`../../ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md`](../../ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md) —
  MADR-format ADR for the InterruptController/InterruptDispatcher split
- [`../../ralph-workflow/CHANGELOG.md`](../../ralph-workflow/CHANGELOG.md) — what changed in the runtime between
  releases
