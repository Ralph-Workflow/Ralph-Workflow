# Rust Implementation Retired

> **Status:** The original Rust binary implementation of Ralph Workflow is
> retired. The maintained implementation is the Python package in
> `ralph-workflow/`.

## What happened to the Rust code

The Ralph Workflow project started as a Rust binary. That implementation has
been retired and is no longer built, published, or supported. All current
development, releases, and documentation target the Python package under
`ralph-workflow/`.

If you are looking for the project that is actively maintained, you want the
Python package.

## Where to start with the Python package

1. Install the current CLI from PyPI:

   ```bash
   pipx install ralph-workflow
   ```

2. Verify the installation:

   ```bash
   ralph --version
   ```

3. Follow the first-run tutorial in the maintained manual:

   - [Getting Started](../ralph-workflow/docs/sphinx/getting-started.md)
   - [Quickstart](../ralph-workflow/docs/sphinx/quickstart.md)
   - [First Task Guide](../ralph-workflow/docs/sphinx/first-task-guide.md)

4. Contributors should start with the package CONTRIBUTING guide:

   - [ralph-workflow/CONTRIBUTING.md](../ralph-workflow/CONTRIBUTING.md)

## Historical Rust-era migration pages

The pages below are kept for historical reference only. Each carries a
quarantine banner at the top of the page noting its Rust-era or deferred
status. They do not describe the maintained Python implementation.

- [policy-v2.md](policy-v2.md) — migration notes for policy format v2.
- [xml-deprecation-timeline.md](xml-deprecation-timeline.md) — retirement
timeline for the legacy XML policy format.
- [plan-xsd-equivalence-notes.md](plan-xsd-equivalence-notes.md) — mapping
from the retired XML plan schema to the current JSON plan schema.
- [xsd-to-json-schema-mapping.md](xsd-to-json-schema-mapping.md) — full
field-by-field mapping from the retired XSD schema to the current JSON
schema.
- [parallel-mode.md](parallel-mode.md) — migration notes for parallel
fan-out.
- [error-response-format.md](error-response-format.md) — proposed canonical
error-response JSON shape; check the status banner before relying on it.
- [weak-model-json-schema-conventions.md](weak-model-json-schema-conventions.md)
— conventions for writing JSON schemas that stay well-formed under weaker
model outputs.

For current schemas and formats, see the operator manual:

- [Configuration reference](../ralph-workflow/docs/sphinx/configuration.md)
- [CLI reference](../ralph-workflow/docs/sphinx/cli.md)
- [Advanced artifact configuration](../ralph-workflow/docs/sphinx/advanced-artifact-configuration.md)

## Primary repo

- Codeberg (primary):
  <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- GitHub (read-only mirror):
  <https://github.com/Ralph-Workflow/Ralph-Workflow>
