# Architecture Docs (package)

Architecture Decision Records (ADRs) and architecture-level notes for
the maintained Python package.

- [ADR-0001: interrupt architecture](adr-0001-interrupt-architecture.md) —
  the maintained ADR for the package's interrupt architecture
- [ADR-0002: visual design verification](adr-0002-visual-design-verification.md) —
  declared renderer, capture matrix, three-input verdict, run-scoped baseline
  (`.agent/PRODUCT_CRITERIA.md` criteria 6–18)
- [ADR-0003: workspace awareness and storage lifecycle](adr-0003-workspace-awareness-and-storage-lifecycle.md) —
  shared bounded observation, explicit fallback freshness, incremental indexing,
  and disposable derived storage
- [ADR-0004: conflict-resolution liveness](adr-0004-conflict-resolution-liveness.md) —
  activity-only supervision, authenticated standalone-MCP activity relay, and
  terminal process quiescence before integration actions
- [ADR-0005: conflict-resolution pipeline parity](adr-0005-conflict-resolution-pipeline-parity.md) —
  shared recovery-controller routing, durable conflict identity, and Ralph-owned
  staging and Git advancement for every conflict path
- [Project Policy Readiness traceability](project-policy-readiness-traceability.md) —
  26-row requirements-traceability matrix binding the spec's acceptance
  criteria to implementing symbols and passing deterministic tests
- [Filesystem activity traceability](../agents/filesystem-activity-traceability.md) —
  baseline evidence and closure map for proportional persistence, reads, and watches
- Runtime internals:
  [`../sphinx/developer-internals.md`](../sphinx/developer-internals.md)