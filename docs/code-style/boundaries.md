# Boundaries

This document defines what boundary code is supposed to be in this repository.

It is a normative guide for functional Rust: what belongs in pure domain code,
what belongs in effectful edge code, how to shape boundary functions, and how to
keep examples compatible with the repository's architecture and lint goals.

## Purpose

Rust does not prevent a function from mixing business decisions, filesystem
access, process spawning, environment lookup, terminal output, and stateful
mutation in one place. This repository does.

The architectural rule is simple:

> Pure code decides. Boundary code executes.

If a function both decides what should happen and performs effects, split it.

## Functional vocabulary

This repository combines a few functional-programming ideas that fit Rust well:

| Idea | Short version | What it means here |
|------|---------------|--------------------|
| Functional core, imperative shell | decisions stay pure | domain code owns rules; edge code owns I/O |
| Impure sandwich | impure -> pure -> impure | boundaries gather inputs, call pure logic, and perform the requested edge interaction |
| Parse, don't validate | keep proofs in types | parse raw input into stronger types at the edge |
| Reader / Writer / Except | dependencies in, diagnostics out, typed failures | no ambient lookups, no hidden side effects, no panic-driven control flow |

These ideas are complementary, not competing:

- `Reader`: resolve dependencies at the edge and pass plain values inward
- `Writer`: return diagnostics as data from pure code when they carry meaning
- `Except`: use `Result` and explicit error types for recoverable failure

## The core rule

Any function that both decides policy and performs effects should become two
things:

1. a pure function that decides, parses, validates, plans, or interprets
2. a thin boundary function that gathers inputs and performs the requested edge interaction

That split is the main boundary rule. Most other rules in this document follow
from it.

## Quick placement guide

| Concern | Primary home | Keep out of |
|---------|--------------|-------------|
| business rules, parsing, planning, interpretation | domain code | `io/`, `runtime/`, `boundary/` |
| filesystem and transport access | `io/` or a `Workspace`-driven boundary | domain code |
| process, env, terminal, clock, OS access | `runtime/` or a boundary over runtime traits | domain code |
| wiring pure logic to one or more capabilities | `boundary/` | reducers and orchestrators |
| workflow progression and retries | orchestrator plus reducer | boundary handlers |

## The standard boundary shape

Well-shaped boundary code usually follows this rhythm:

```text
1. IMPURE  - gather inputs from capabilities
2. PURE    - parse, normalize, decide, or interpret
3. IMPURE  - perform the requested edge interaction or emit diagnostics
```

The boundary should read like wiring:

> "Read X, call pure logic with X, then apply the returned result."

The interesting logic should live in the pure middle step, not in the outer
effectful shell.

## What boundary code must do

Boundary code should:

- accept explicit capabilities and plain data as inputs
- gather raw inputs from the outside world
- call pure helpers on ordinary Rust values
- execute the requested edge interaction without owning overall workflow policy
- translate capability failures into typed application failures
- return typed results, events, or parsed values that callers can reduce or act on

Boundary code may contain small structural control flow when needed for wiring:

- `?` for failure propagation
- `match` on transport or effect enums
- `let ... else` for short wiring exits
- straightforward result mapping

The rule is not "no control flow at all." The rule is "no business policy in
the control flow."

## What boundary code must not do

Boundary code must not own domain policy.

In practice, that means boundary functions must not contain:

- business branching
- retry or fallback policy
- workflow progression decisions
- invariant enforcement that should have been captured in a parsed type
- state-machine logic that belongs in an orchestrator or reducer
- hidden dependency lookups that make the function appear pure when it is not

If the boundary needs comments to explain the business rule it is applying, the
logic is probably in the wrong place.

## Type-driven design at the edge

Boundaries should convert raw input into domain-shaped types as early as
possible.

Prefer this:

```rust
pub struct RetryCount(u32);

pub struct NonEmptyTargets {
    first: String,
    rest: Vec<String>,
}

pub struct DeployConfig {
    pub retries: RetryCount,
    pub targets: NonEmptyTargets,
}
```

over this:

```rust
pub struct RawDeployConfig {
    pub retries: u32,
    pub targets: Vec<String>,
}
```

The point is not academic purity. The point is removing repeated checks from the
rest of the program.

### Parse, don't validate

Validation only answers "is this okay?" Parsing answers "what stronger thing do
we have now?"

Prefer parsing raw values into stronger domain values:

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NonEmptyItems {
    first: String,
    rest: Vec<String>,
}

pub fn parse_non_empty_items(items: Vec<String>) -> Result<NonEmptyItems, ParseError> {
    items
        .split_first()
        .map(|(first, rest)| NonEmptyItems {
            first: first.clone(),
            rest: rest.to_vec(),
        })
        .ok_or(ParseError::EmptyItems)
}
```

Once parsing succeeds, downstream code no longer needs to ask whether the list
is empty. The proof travels in the type.

### Newtypes and small enums

Use newtypes and explicit enums when primitive values carry stable business
meaning.

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TemplatePath(pub String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutputPath(pub String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReviewMode {
    Quick,
    Full,
}
```

This keeps the domain explicit and reduces invalid combinations.

Newtypes solve naming and distinction first. They only enforce stronger
invariants when combined with private fields plus smart constructors,
`TryFrom`, or parser functions.

## Module roles

Use module names as architectural categories, not as lint escape hatches.

### Domain code

Domain modules own pure logic:

- parsing from already-available strings or values into domain types
- validation and normalization
- planners and selectors
- reducers and state transitions
- interpretation of effect results when that interpretation is pure
- fact and effect modeling

Domain code must not:

- read or write files
- inspect environment variables
- read the current working directory
- spawn processes
- print to stdout or stderr
- read clocks or sleep

### `io/`

Use `io/` for filesystem and transport-facing work.

Typical responsibilities:

- reading and writing raw bytes or text
- talking to the `Workspace` capability
- mapping storage or transport failures into typed I/O errors
- loading raw external representations so pure code can parse them

Typical non-responsibilities:

- domain policy
- retry decisions
- workflow decisions
- domain invariant checks that belong in pure parsers

Important distinction:

- `io/` may decode transport enough to produce raw strings, bytes, or transport
  records
- domain code owns the parse step that gives those values application meaning

### `runtime/`

Use `runtime/` for OS-facing capabilities.

Typical responsibilities:

- process execution
- environment access
- terminal integration
- clocks, sleeping, and timing
- OS error capture

Typical non-responsibilities:

- deciding whether a command should run
- deciding what a runtime result means for the business domain
- choosing retries or fallbacks

### `boundary/`

Use `boundary/` for thin composition seams.

Typical responsibilities:

- gather inputs from `io/`, `runtime/`, or other capabilities
- call pure planners, parsers, reducers, or interpreters
- perform the requested edge interaction
- emit diagnostics that pure code returned as data
- translate capability errors into boundary-facing error types

Typical non-responsibilities:

- hiding workflow logic
- accumulating many unrelated jobs in one module
- reimplementing parsing and validation inline
- becoming a dumping ground for all non-pure code

### `ffi/`

Use `ffi/` only for foreign-library boundaries. It is not a general-purpose
escape hatch.

## Workspace rule

For filesystem work in production code, the repository rule is stronger than
"prefer traits":

> Filesystem access must go through `Workspace`, except for documented
> bootstrap or implementation exceptions.

That means ordinary production code should treat `Workspace` as the boundary
capability for file access.

Prefer this:

```rust
pub fn load_template(
    workspace: &dyn Workspace,
    path: &str,
) -> Result<String, LoadTemplateError> {
    workspace
        .read(path)
        .map_err(LoadTemplateError::from)
}
```

not direct `std::fs::*` calls in ordinary code.

These examples are schematic. When using the real repository API, follow the
actual `Workspace` trait shape and documented exceptions in
`docs/agents/workspace-trait.md`.

## Boundaries in the architecture flow

The architecture document defines this shape:

```text
State -> Orchestrator -> Effect -> Handler -> Event -> Reducer -> State
```

In that flow:

- the orchestrator is pure and chooses the next effect
- the handler or boundary performs the requested edge interaction
- in reducer-driven flows, handlers report the outcome as descriptive events
- the reducer is pure and computes the next state

This means boundary code does not decide what happens next. It performs the
requested edge interaction and reports what happened in the shape the caller or
architecture expects.

## Worked examples

### Example 1: Filesystem input -> parse -> typed config

This is the baseline shape for file-backed input.

```rust
// domain/config.rs
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub template_name: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfigParseError {
    EmptyTemplateName,
    InvalidFormat,
}

pub fn parse_config(raw: &str) -> Result<Config, ConfigParseError> {
    let line = raw
        .lines()
        .next()
        .ok_or(ConfigParseError::InvalidFormat)?;

    (!line.trim().is_empty())
        .then(|| Config {
            template_name: line.trim().to_string(),
        })
        .ok_or(ConfigParseError::EmptyTemplateName)
}
```

```rust
// boundary/load_config.rs
pub fn load_config(
    workspace: &dyn Workspace,
    path: &str,
) -> Result<Config, LoadConfigError> {
    let raw = workspace.read(path).map_err(LoadConfigError::Read)?;

    parse_config(&raw).map_err(LoadConfigError::Parse)
}
```

The important split is:

- boundary reads the raw text
- pure parser gives that text domain meaning
- callers receive a typed `Config`, not an unchecked string

### Example 2: Planner + runtime execution + pure interpretation

Pure code should decide what command to run and what the output means.

```rust
// domain/formatting.rs
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandSpec {
    pub program: String,
    pub args: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandOutput {
    pub success: bool,
    pub stderr: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FormatEvent {
    FormattingCompleted,
    FormattingRejected(String),
}

pub fn plan_formatter(path: &str) -> CommandSpec {
    CommandSpec {
        program: "rustfmt".to_string(),
        args: vec![path.to_string()],
    }
}

pub fn interpret_formatter_output(output: CommandOutput) -> FormatEvent {
    if output.success {
        FormatEvent::FormattingCompleted
    } else {
        FormatEvent::FormattingRejected(output.stderr)
    }
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
    path: &str,
) -> Result<FormatEvent, RunFormatterError> {
    let command = plan_formatter(path);

    executor
        .run(&command)
        .map_err(RunFormatterError::Execution)
        .map(interpret_formatter_output)
}
```

The boundary performs the requested runtime interaction. It does not decide
retries, fallbacks, or what phase should run next.

### Example 3: Environment lookup at the edge

Environment access is a boundary concern even when it looks like a simple
helper.

```rust
// runtime/environment.rs
pub trait ConfigEnvironment {
    fn template_dir(&self) -> Result<Option<String>, EnvironmentError>;
}
```

```rust
// domain/settings.rs
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TemplateDirectory(String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TemplateDirectoryParseError {
    Empty,
}

pub fn parse_template_directory(raw: String) -> Result<TemplateDirectory, TemplateDirectoryParseError> {
    let normalized = raw.trim().to_string();

    (!normalized.is_empty())
        .then(|| TemplateDirectory(normalized))
        .ok_or(TemplateDirectoryParseError::Empty)
}
```

```rust
// boundary/load_template_directory.rs
pub fn load_template_directory(
    environment: &dyn ConfigEnvironment,
) -> Result<TemplateDirectory, LoadTemplateDirectoryError> {
    let raw = environment
        .template_dir()
        .map_err(LoadTemplateDirectoryError::Environment)?
        .ok_or(LoadTemplateDirectoryError::Missing)?;

    parse_template_directory(raw).map_err(LoadTemplateDirectoryError::Parse)
}
```

The boundary owns the lookup. The pure function owns the meaning.

### Example 4: Diagnostics as data, emitted later

Pure functions often need to explain what they normalized or defaulted. Return
those diagnostics as values.

```rust
// domain/review_settings.rs
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReviewDiagnostic {
    UsedDefaultTimeout,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Logged<T> {
    pub value: T,
    pub diagnostics: Vec<ReviewDiagnostic>,
}

pub fn normalize_timeout(raw_timeout: Option<u64>) -> Logged<u64> {
    match raw_timeout {
        Some(timeout) => Logged {
            value: timeout,
            diagnostics: Vec::new(),
        },
        None => Logged {
            value: 30,
            diagnostics: vec![ReviewDiagnostic::UsedDefaultTimeout],
        },
    }
}
```

```rust
// boundary/load_review_settings.rs
pub fn load_review_timeout(
    logger: &dyn Logger,
    raw_timeout: Option<u64>,
) -> u64 {
    let logged = normalize_timeout(raw_timeout);

    logged
        .diagnostics
        .iter()
        .for_each(|diagnostic| logger.emit(diagnostic));

    logged.value
}
```

The boundary emits. The domain explains.

### Example 5: Retry belongs in orchestration, not the boundary

This is a common mistake.

Bad shape:

```rust
pub fn run_with_retries(
    executor: &dyn ProcessExecutor,
    command: &CommandSpec,
    max_retries: u32,
) -> Result<CommandOutput, ProcessExecutionError> {
    let mut attempts = 0;

    loop {
        match executor.run(command) {
            Ok(output) => return Ok(output),
            Err(error) if attempts < max_retries => {
                attempts += 1;
                let _ = error;
            }
            Err(error) => return Err(error),
        }
    }
}
```

The loop is not the main problem. The architectural problem is that this code
owns policy: should we retry, and when do we stop?

Correct shape:

- pure orchestration decides whether a retry effect should be scheduled
- boundary executes one attempt
- reducer consumes the resulting event and updates state

The decision to retry belongs in the state machine, not in the executor shell.

## Testing strategy

The boundary split gives two natural testing layers.

### Pure tests

Pure functions should be testable with plain values:

- parsers from strings to typed values
- planners from state or input to effect requests
- interpreters from raw outputs to typed outcomes or fact-shaped events
- reducers from `(state, event)` to `state`

These tests should not need trait objects, mocks, or I/O setup.

### Boundary tests

Boundary tests should verify wiring:

- the right capability is called
- capability errors map into the right boundary errors
- returned diagnostics are emitted
- the boundary produces the expected event, parsed value, or translated output

When filesystem access is involved, test through `Workspace`-shaped fakes rather
than direct `std::fs` calls.

## Quick checklist

Ask these questions when placing or reviewing code:

1. Can this logic run entirely on plain values? Put it in domain code.
2. Does it read or write files through repository infrastructure? Put the effect
   edge in `io/` or a `Workspace`-driven boundary.
3. Does it talk to process, env, terminal, clock, or OS state? Put that effect
   edge in `runtime/` or a boundary that depends on a runtime trait.
4. Is it mostly wiring pure logic to one or more capabilities? Put it in
   `boundary/`.
5. Does the function decide what should happen next in the workflow? That is
    orchestration, not boundary code.
6. Would removing I/O leave the interesting part intact? If yes, extract that
    part and keep it pure.

## Common mistakes

### Mixing business branching into boundary code

Good boundary control flow is structural. Bad boundary control flow decides
policy.

- good: map capability errors, switch on an effect enum, early-return missing raw input
- bad: choose deployment strategy, decide retries, classify business outcome inline

### Scattering validation across the edge

If field presence checks and domain invariants are repeated in several boundary
functions, the input has not been parsed into the right type yet.

### Hiding ambient lookups in "helpers"

A function that calls `std::env::var`, reads the current directory, or touches
the filesystem is not pure just because it has a short name.

### Treating `boundary/` as a dumping ground

Boundary modules should stay small and workflow-focused. Do not move ordinary
domain code there just to bypass functional constraints.

### Confusing transport decoding with domain meaning

Reading bytes, lines, or files is boundary work. Deciding what those values mean
for the program is domain work.

### Using panics where typed failure belongs

Recoverable failure should normally travel through `Result`, not `unwrap()`,
`expect()`, or hidden process termination.

## Final rule of thumb

Good boundary code is boring.

It gathers inputs, calls pure code, performs the requested edge interaction, and
returns a typed result.

If the boundary is where the interesting reasoning lives, the split is not done
yet.
