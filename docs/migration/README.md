# Migration Notes

This page is the migration-doc index for readers moving between ralph-workflow policy versions.
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
- **[weak-model-json-schema-conventions.md](weak-model-json-schema-conventions.md)** —
  conventions for writing JSON schemas that stay well-formed under
  weaker model outputs.

The three Rust-era pages that previously lived in this directory
(`xml-deprecation-timeline.md`, `plan-xsd-equivalence-notes.md`,
`xsd-to-json-schema-mapping.md`, and the design-proposal
`error-response-format.md`) have been moved to
[`../legacy-rust/migration/`](../legacy-rust/migration/README.md) —
they describe a never-shipped Rust deserializer and the corresponding
maintained Python artifact reality is
`ralph-workflow/ralph/mcp/artifacts/`. The parallel-mode migration
notes were consolidated into the canonical
[`ralph-workflow/docs/sphinx/parallel-mode.md`](../../ralph-workflow/docs/sphinx/parallel-mode.md)
page.

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
