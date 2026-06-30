# Migration Notes

This directory collects migration notes for Ralph Workflow.

## Who this is for

Contributors and operators working through a planned migration of the
Python package — most often a policy-version upgrade, a JSON schema
change, or a transition between toolchain formats.

If you are looking for the retired Rust implementation, see
[Rust Implementation Retired](rust-implementation-retired.md) first.

## Read this first

- **[policy-v2.md](policy-v2.md)** — migration notes for policy
  format v2 (recommended for any new workflow).
- **[xml-deprecation-timeline.md](xml-deprecation-timeline.md)** — the
  retirement timeline for the legacy XML policy format. **Legacy
  Rust-era (historical):** references `json_artifact.rs` and a
  never-shipped Rust deserializer. Quarantined banner at the top of
  the page; the maintained Python artifact reality is
  `ralph-workflow/ralph/mcp/artifacts/`.
- **[plan-xsd-equivalence-notes.md](plan-xsd-equivalence-notes.md)** —
  equivalence mapping from the retired XML plan schema to the current
  JSON plan schema. **Legacy Rust-era (historical):** references
  `src/prompts/xsd/plan.xsd` and the never-shipped Rust deserializer.
  Quarantined banner at the top of the page.
- **[xsd-to-json-schema-mapping.md](xsd-to-json-schema-mapping.md)** —
  full field-by-field mapping from the retired XSD schema to the
  current JSON schema. **Legacy Rust-era (historical):** describes a
  Rust `xsd_validation_*` module that never shipped in any released
  Ralph build. Quarantined banner at the top of the page.
- **[parallel-mode.md](parallel-mode.md)** — migration notes for
  enabling or upgrading parallel fan-out.
- **[error-response-format.md](error-response-format.md)** — migration
  notes for the canonical error-response JSON shape. See the status
  banner at the top of the page before relying on the proposed
  shape.
- **[weak-model-json-schema-conventions.md](weak-model-json-schema-conventions.md)** —
  conventions for writing JSON schemas that stay well-formed under
  weaker model outputs.

## Next click

For the current maintained schemas and formats, see the operator
manual:

- [Configuration reference](../../ralph-workflow/docs/sphinx/configuration.md)
- [CLI reference](../../ralph-workflow/docs/sphinx/cli.md)
- [Advanced artifact configuration](../../ralph-workflow/docs/sphinx/advanced-artifact-configuration.md)

## Primary repo

- Codeberg (primary):
  <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- GitHub (read-only mirror):
  <https://github.com/Ralph-Workflow/Ralph-Workflow>
