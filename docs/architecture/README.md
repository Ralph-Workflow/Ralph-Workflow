# Architecture Docs

This page is the architecture-doc index for readers who need to understand Ralph Workflow's system boundaries before editing code.

This directory holds the **current Python-runtime architecture documentation**.
For archived Rust-era design material, see
[`../legacy-rust/README.md`](../legacy-rust/README.md).

## Read this first

- **[overview.md](overview.md)** — end-to-end Python-runtime architecture
  overview: subsystem boundaries, ownership, data flow, and invariants
  for every layer from the CLI through the MCP server.

## Maintained: Current Python Behavior

- **[overview.md](overview.md)** — End-to-end Python-runtime architecture:
  Ralph loop, policy interpretation, phase routing, agent invocation,
  artifact submission, completion detection, verification, recovery,
  watchdogs, configuration, and extension points.

The detailed pipeline-lifecycle and event-loop-and-reducers material
previously kept under `docs/architecture/` was folded into
[`ralph-workflow/docs/sphinx/developer-internals.md`](../../ralph-workflow/docs/sphinx/developer-internals.md)
so the maintained Python contributor doc has one home per topic. Use
`overview.md` for the high-level subsystem map; the developer-internals
page for the runtime internals.

## Quarantined: Rust-era material

All Rust-era architecture material is quarantined under
[`../legacy-rust/architecture/`](../legacy-rust/architecture/) with a clear
header noting it is unmaintained pre-Python-rewrite reference. Do not rely
on those pages for current behavior.

## Related

- [`../../ralph-workflow/docs/sphinx/`](../../ralph-workflow/docs/sphinx/index.rst) — the maintained Sphinx manual
- [`../../ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md`](../../ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md) — MADR-format ADR for the InterruptController/InterruptDispatcher split
- [`../../ralph-workflow/CHANGELOG.md`](../../ralph-workflow/CHANGELOG.md) — what changed in the runtime between releases
