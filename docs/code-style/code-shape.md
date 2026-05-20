# Code Shape

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **Historical Rust-era documentation** — This file describes the retired Rust implementation's code shape guidance. The current Python package under `ralph-workflow/` follows different patterns. Treat this file as archival background only.

This document defines what the Ralph codebase should look like.

## What "done" means

Finished code has these traits:

- domain logic is pure and deterministic
- reducers and orchestration decide policy without doing I/O
- side effects live in narrow boundary modules
- modules are organized by responsibility, not convenience
- tests target the right layer with the right kind of doubles
- code reads like explicit data transformation, not step-by-step mutation

## Architecture

The architecture is:

```text
inputs/config/events
  -> pure parsing/validation/modeling
  -> pure reducer/orchestration decisions
  -> thin boundary interpretation
  -> filesystem/process/logging/output effects
```

The code should answer these questions cleanly:

- What happened?
- What does that mean?
- What should happen next?
- Which boundary performs the effect?

Those answers should not be mixed into one function.

## Properties by layer

| Layer | Responsibility | Must not do |
|------|---------------------------|-------------|
| Domain | Parse, validate, transform, model, decide | Read files, spawn processes, inspect env, print |
| Reducers | `(state, event) -> new_state` | I/O, retries in loops, hidden global state |
| Orchestration | Decide next effect from state | Perform the effect directly |
| `io/` | Filesystem and external text/bytes translation | Business policy |
| `runtime/` | Process, time, env, OS interaction | Business policy |
| `boundary/` | Wire domain decisions to concrete capabilities | Re-encode domain rules inline |

## The preferred feel of finished code

### Pure state transition

```rust
pub fn with_event(self, event: ReviewEvent) -> Self {
    match event {
        ReviewEvent::Started => Self {
            started: true,
            ..self
        },
        ReviewEvent::IssueRecorded(issue) => Self {
            issues: self.issues.into_iter().chain([issue]).collect(),
            ..self
        },
    }
}
```

This is the target feel:

- input value in
- output value out
- no ambient dependencies
- no hidden mutation

### Full reducer slice

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReviewIssue {
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ReviewState {
    pub started: bool,
    pub completed: bool,
    pub issues: Vec<ReviewIssue>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReviewEvent {
    Started,
    IssueRecorded(ReviewIssue),
    Completed,
}

impl ReviewState {
    #[must_use]
    pub fn with_event(self, event: ReviewEvent) -> Self {
        match event {
            ReviewEvent::Started => Self {
                started: true,
                ..self
            },
            ReviewEvent::IssueRecorded(issue) => Self {
                issues: self.issues.into_iter().chain([issue]).collect(),
                ..self
            },
            ReviewEvent::Completed => Self {
                completed: true,
                ..self
            },
        }
    }
}

#[must_use]
pub fn reduce_review(state: ReviewState, event: ReviewEvent) -> ReviewState {
    state.with_event(event)
}
```

This is the ideal reducer feel for the project:

- facts come in as events
- state transitions stay explicit
- all policy lives in pure functions
- the reducer can be tested with plain values only

### Full workflow slice

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

The important property is separation:

- state and events define the domain vocabulary
- reducer applies facts to state
- orchestration decides the next effect
- boundary code performs the interaction
- runtime code owns the OS-facing capability

### Thin boundary interpreter

```rust
pub fn materialize_review_input(
    workspace: &dyn Workspace,
    request: &ReviewInputRequest,
) -> Result<ReviewInput, ReviewInputError> {
    workspace
        .read(request.plan_path.as_str())
        .map_err(|_| ReviewInputError::MissingPlan(request.plan_path.clone()))
        .and_then(|plan| build_review_input(request, &plan))
}
```

This is also the target feel:

- gather external input
- call pure logic
- translate errors
- stop

### Review flow example

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReviewEffect {
    ExecuteReview(ReviewRequest),
}

#[must_use]
pub fn determine_review_effect(state: &ReviewState) -> Option<ReviewEffect> {
    (!state.started).then(|| {
        ReviewEffect::ExecuteReview(ReviewRequest {
            prompt: "review current plan".to_string(),
        })
    })
}
```

```rust
pub fn run_review(
    executor: &dyn ReviewExecutor,
    request: &ReviewRequest,
) -> Result<ReviewEvent, ReviewExecutionError> {
    executor
        .run_review(request)
        .map(|output| {
            output
                .issue
                .map_or(ReviewEvent::Completed, ReviewEvent::IssueRecorded)
        })
}
```

The ideal split is visible here too:

- orchestration returns an effect description
- runtime capability executes the work
- boundary function translates output to an event
- reducer owns the resulting state change

## End-state checklist

Code is in the desired shape when most functions satisfy all of these:

- name reflects one responsibility
- arguments contain all required inputs
- return type models success and failure explicitly
- tests can exercise the core logic with plain values
- the function either decides or executes, but not both
- example code can live in ordinary non-boundary modules without lint suppressions

## Non-goals of the end state

The desired style is not:

- abstraction for its own sake
- replacing every `match` with combinator chains
- introducing Haskell terminology into ordinary code
- moving code into `boundary/` only to silence lints
- generic programming where explicit Rust is clearer

If explicit structs, enums, and `match` statements are the clearest design, that is the preferred end state.
