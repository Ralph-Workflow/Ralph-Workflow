# Module Organization

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **Historical Rust-era documentation** — This file describes the retired Rust implementation's module organization guidance. The current Python package under `ralph-workflow/` follows different patterns. Treat this file as archival background only.

This document describes how finished modules should be shaped once refactoring is complete.

The examples here are normative examples for this codebase.

## Organize by responsibility, not by line count alone

The goal of splitting is not to produce many small files. The goal is to produce files that each own one stable reason to change.

Good split triggers:

- one file mixes parsing, validation, execution, and rendering
- one file has multiple conceptual phases
- one file owns both domain rules and boundary translation
- one file needs section comments just to explain its internal neighborhoods

Bad split triggers:

- a cohesive reducer match is large but still one concept
- a type definition file is long because the type is legitimately large
- a file is slightly over a line guideline but structurally clear

## Preferred shapes

### One workflow, multiple phases

```text
review/
  mod.rs
  inputs.rs
  planning.rs
  rendering.rs
  validation.rs
```

Use this when one workflow has distinct phases with different responsibilities.

### Domain package with explicit responsibilities

```text
config/
  mod.rs
  model.rs
  parse.rs
  validate.rs
  normalize.rs
```

Use this when one domain concept has several pure transformations.

### Boundary package with focused seams

```text
boundary/
  load_config.rs
  run_formatter.rs
  emit_logs.rs
```

Use this when each boundary function coordinates a different external interaction.

### Reducer package

```text
review/
  mod.rs
  state.rs
  event.rs
  reduce.rs
  selectors.rs
  orchestrate.rs
```

Use this when one workflow owns:

- its own state shape
- its own fact vocabulary
- its own selectors
- its own effect-selection logic

### Handler package

Handlers are thin effect-execution shims.  A handler file coordinates one specific
external interaction by gathering inputs, delegating to pure logic, and performing
the effect.  Avoid nesting handlers into subdirectory trees — keep handler modules
flat, following the same flat-boundary rule as `boundary/`.

## End-state module examples

### Preferred split for a plan-writing workflow

```rust
// domain/plan/normalize.rs
pub fn normalize_plan(raw: &str) -> Result<String, PlanError> {
    (!raw.trim().is_empty())
        .then(|| raw.lines().map(str::trim).collect::<Vec<_>>().join("\n"))
        .ok_or(PlanError::Empty)
}
```

```rust
// io/plan_writer.rs
pub fn write_plan(
    workspace: &dyn Workspace,
    path: &Utf8Path,
    contents: &str,
) -> Result<(), PlanError> {
    workspace
        .write(path.as_str(), contents)
        .map_err(|_| PlanError::WriteFailed)
}
```

```rust
// boundary/save_plan.rs
pub fn save_plan(
    workspace: &dyn Workspace,
    path: &Utf8Path,
    raw: &str,
) -> Result<(), PlanError> {
    normalize_plan(raw).and_then(|normalized| write_plan(workspace, path, &normalized))
}
```

### Preferred split for a reducer-oriented workflow

```text
review/
  mod.rs
  state.rs
  event.rs
  reduce.rs
  orchestrate.rs

boundary/
  run_review.rs

runtime/
  review_executor.rs
```

Use this shape when one workflow has:

- state that evolves over time
- fact-shaped events
- pure effect selection
- one or more runtime capabilities

### End-state reducer package example

```rust
//! Review workflow.

mod event;
mod orchestrate;
mod reduce;
mod state;

pub(crate) use self::event::ReviewEvent;
pub(crate) use self::orchestrate::determine_review_effect;
pub(crate) use self::reduce::reduce_review;
pub(crate) use self::state::ReviewState;
```

This is better than a single giant file when the workflow clearly separates into vocabulary, state transition, and effect selection.

### Preferred package for a full pipeline phase

```text
reducer/
  review/
    mod.rs
    state.rs
    event.rs
    selectors.rs
    reduce.rs
    orchestrate.rs

boundary/
  run_review.rs
```

This split keeps policy code and execution code separate without fragmenting either one into random helpers.  Boundary files are flat — one file per effect seam, not a nested tree.

## `mod.rs` guidance

Use `mod.rs` to:

- declare internal modules
- provide module-level docs
- re-export the stable public surface

Do not use `mod.rs` to hide unrelated utilities that should be separate modules.

Example:

```rust
//! Review workflow modules.

mod inputs;
mod planning;
mod rendering;
mod validation;

pub(crate) use self::inputs::materialize_review_inputs;
pub(crate) use self::planning::build_review_request;
pub(crate) use self::rendering::render_review_report;
pub(crate) use self::validation::validate_review_output;
```

## File naming rules

Prefer names that describe responsibility directly:

- `parse.rs`
- `validate.rs`
- `normalize.rs`
- `inputs.rs`
- `event.rs`
- `state.rs`
- `selectors.rs`
- `reduce.rs`
- `orchestrate.rs`
- `rendering.rs`
- `execution.rs`
- `output.rs`
- `validation.rs`

Avoid vague names unless the file truly is the main coordinator:

- `utils.rs`
- `helpers.rs`
- `misc.rs`
- `common.rs`

If a name like `helpers.rs` is necessary, the file is probably not yet split enough.
