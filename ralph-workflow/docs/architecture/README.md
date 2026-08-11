# Architecture Docs (package)

Architecture Decision Records (ADRs) and architecture-level notes for
the maintained Python package.

- [ADR-0001: interrupt architecture](adr-0001-interrupt-architecture.md) —
  the maintained ADR for the package's interrupt architecture
- [ADR-0002: visual design verification](adr-0002-visual-design-verification.md) —
  declared renderer, capture matrix, three-input verdict, run-scoped baseline
  (`.agent/PRODUCT_CRITERIA.md` criteria 6–18)
- [Project Policy Readiness traceability](project-policy-readiness-traceability.md) —
  26-row requirements-traceability matrix binding the spec's acceptance
  criteria to implementing symbols and passing deterministic tests
- [Filesystem activity traceability](../agents/filesystem-activity-traceability.md) —
  baseline evidence and closure map for proportional persistence, reads, and watches
- Runtime internals:
  [`../sphinx/developer-internals.md`](../sphinx/developer-internals.md)