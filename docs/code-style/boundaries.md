# Boundaries

This document defines where effectful code belongs and what shape it should take.

All code snippets here are intended to model compliant project code for this repository.

## Core rule

If a function both decides what should happen and performs I/O, split it.

The preferred split is:

1. pure domain function that decides, validates, or transforms
2. boundary function that gathers inputs and executes effects

## Module roles

### Domain modules

Domain modules own:

- parsing from already-available strings or values
- validation and normalization
- reducer transitions
- effect planning
- result interpretation when the interpretation itself is pure

Domain modules must not:

- call `std::fs`
- inspect environment variables
- read the current working directory
- spawn processes
- print to stdout/stderr
- read the clock

### `io/`

Use `io/` for filesystem and external data transport work.

Typical responsibilities:

- reading or writing through `Workspace`
- translating file presence/absence into typed errors
- loading raw text or bytes
- serializing or deserializing transport formats at the edge

Typical non-responsibilities:

- deciding retry policy
- choosing pipeline transitions
- embedding business validation rules beyond transport checks

### `runtime/`

Use `runtime/` for OS-facing capabilities.

Typical responsibilities:

- process spawning
- time and sleeping
- environment access
- terminal integration
- OS error capture

Typical non-responsibilities:

- deciding whether a command should run
- deciding what a process result means for business logic

### Logging boundaries

Logging should follow the same split as other effects.

- domain code returns diagnostics as values when they are part of meaning
- boundary code emits those diagnostics through a logger or UI sink
- runtime logging helpers should not invent business semantics

### `boundary/`

Use `boundary/` for thin composition seams.

Typical responsibilities:

- gather inputs from one or more capabilities
- call pure domain helpers
- emit returned logs or diagnostics
- translate between capability errors and domain errors

Typical non-responsibilities:

- deep business branching
- reimplementing validation rules inline
- accumulating unrelated workflows into one large module

### `ffi/`

Use `ffi/` only for foreign-library boundaries. Do not treat it as a generic escape hatch.

## Placement examples

### Ideal project layout for one workflow

```text
review/
  state.rs
  event.rs
  reduce.rs
  orchestrate.rs

io/
  review_input_loader.rs

runtime/
  review_executor.rs

boundary/
  run_review.rs
```

This is the preferred final shape when a workflow needs both pure policy code and effectful execution.

### Filesystem workflow

```rust
// domain/config/parse.rs
pub fn parse_config(contents: &str) -> Result<AppConfig, ConfigParseError> {
    toml::from_str(contents)
        .map_err(|error| ConfigParseError::InvalidToml(error.to_string()))
}
```

```rust
// io/config_loader.rs
pub fn read_config(
    workspace: &dyn Workspace,
    path: &Utf8Path,
) -> Result<String, LoadConfigError> {
    workspace
        .read(path.as_str())
        .map_err(|_| LoadConfigError::MissingFile(path.to_owned()))
}
```

```rust
// boundary/load_config.rs
pub fn load_config(
    workspace: &dyn Workspace,
    path: &Utf8Path,
) -> Result<AppConfig, LoadConfigError> {
    read_config(workspace, path)
        .and_then(|contents| parse_config(&contents).map_err(LoadConfigError::from))
}
```

### Process workflow

```rust
// domain/formatting/plan.rs
pub fn formatter_command(path: &Utf8Path) -> CommandSpec {
    CommandSpec {
        program: "rustfmt".to_string(),
        args: vec![path.as_str().to_string()],
    }
}

pub fn interpret_formatter_output(
    output: CommandOutput,
) -> Result<FormatOutcome, FormatError> {
    output
        .success
        .then_some(FormatOutcome::Formatted)
        .ok_or(FormatError::FormatterRejected(output.stderr))
}
```

```rust
// runtime/process_executor.rs
pub trait ProcessExecutor {
    fn run(&self, command: &CommandSpec) -> Result<CommandOutput, ProcessExecutionError>;
}
```

```rust
// boundary/run_formatter.rs
pub fn run_formatter(
    executor: &dyn ProcessExecutor,
    path: &Utf8Path,
) -> Result<FormatOutcome, FormatError> {
    let command = formatter_command(path);
    executor
        .run(&command)
        .map_err(|_| FormatError::SpawnFailed)
        .and_then(interpret_formatter_output)
}
```

### Combined reducer-boundary example

```rust
// review/orchestrate.rs
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReviewEffect {
    Execute(ReviewRequest),
}

#[must_use]
pub fn determine_effect(state: &ReviewState) -> Option<ReviewEffect> {
    (!state.started).then(|| {
        ReviewEffect::Execute(ReviewRequest {
            prompt: "review the current implementation".to_string(),
        })
    })
}
```

```rust
// boundary/run_review.rs
pub fn run_review(
    executor: &dyn ReviewExecutor,
    effect: &ReviewEffect,
) -> Result<ReviewEvent, ReviewExecutionError> {
    match effect {
        ReviewEffect::Execute(request) => executor.run_review(request).map(|output| {
            output.issue.map_or(ReviewEvent::Completed, ReviewEvent::IssueRecorded)
        }),
    }
}
```

This is the intended relationship:

- pure code decides whether review should run
- boundary code executes that decision once
- reducer code applies the resulting fact
- no single function owns all three jobs

### Environment lookup pattern

Environment access belongs in `runtime/` or a boundary-facing environment trait, not in pure helpers.

```rust
// runtime/config_environment.rs
pub trait ConfigEnvironment {
    fn max_retries(&self) -> Result<Option<u32>, ConfigEnvironmentError>;
}
```

```rust
// domain/config/build.rs
pub fn build_retry_policy(input: RetryPolicyInput) -> RetryPolicy {
    RetryPolicy {
        max_retries: input.max_retries,
    }
}
```

```rust
// boundary/load_retry_policy.rs
pub fn load_retry_policy(
    environment: &dyn ConfigEnvironment,
) -> Result<RetryPolicy, LoadRetryPolicyError> {
    environment
        .max_retries()
        .map_err(LoadRetryPolicyError::Environment)?
        .map(|max_retries| build_retry_policy(RetryPolicyInput { max_retries }))
        .ok_or(LoadRetryPolicyError::MissingMaxRetries)
}
```

## Decision guide

Ask these in order:

1. Can this run with plain values only? Put it in domain code.
2. Does it talk to files or `Workspace`? Put it in `io/`.
3. Does it talk to process/env/time/OS capabilities? Put it in `runtime/`.
4. Is it mostly wiring domain code to one or more capabilities? Put it in `boundary/`.

## Common mistakes

- putting parsing policy in `io/`
- putting business decisions in `runtime/`
- creating a huge `boundary.rs` dumping ground
- hiding environment lookup in helpers that appear pure
- moving ordinary domain code into boundary paths only to bypass lints
