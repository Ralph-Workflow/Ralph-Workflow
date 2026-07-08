# Architecture Docs

This page is the architecture-doc index for readers who need to understand Ralph Workflow's system boundaries before editing code.

This directory holds the **current Python-runtime architecture documentation**. The Rust-era material lives under [`../legacy-rust/`](../legacy-rust/README.md) as unmaintained pre-Python-rewrite reference — do not rely on it for current behavior.

## Maintained

- **[overview.md](overview.md)** — high-level subsystem map: Ralph loop, policy interpretation, phase routing, agent invocation, artifact submission, completion detection, verification, recovery, watchdogs, configuration, and extension points.
- **Runtime internals:** [`ralph-workflow/docs/sphinx/developer-internals.md`](../../ralph-workflow/docs/sphinx/developer-internals.md) covers the pipeline lifecycle, event loop and reducers, configuration loading, streaming blocks, and the supervising API in one page.

## Related

- [`../../ralph-workflow/docs/sphinx/`](../../ralph-workflow/docs/sphinx/index.rst) — the maintained Sphinx manual
- [`../../ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md`](../../ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md) — MADR-format ADR for the InterruptController/InterruptDispatcher split
- [`../../ralph-workflow/CHANGELOG.md`](../../ralph-workflow/CHANGELOG.md) — what changed in the runtime between releases