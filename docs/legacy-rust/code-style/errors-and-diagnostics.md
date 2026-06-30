# Errors And Diagnostics

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **Historical Rust-era documentation** — This file describes the retired Rust implementation's error handling patterns. The current Python package under `ralph-workflow/` follows different patterns. Treat this file as archival background only.

This document defines how the project should model failures, warnings, and domain diagnostics.

## Core rule

Failures are values.

That means project code should prefer:

- typed error enums over stringly failures
- `Result<T, E>` over panics
- diagnostic values returned from pure code over direct printing
- boundary emission of logs and warnings after domain decisions are complete

## Error shape

Use small enums that reflect domain meaning.

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReviewPreparationError {
    MissingPlan,
    EmptyPlan,
    InvalidIssueFormat,
}
```

Prefer this over:

- `String` errors for core domain flows
- a single giant catch-all error enum for unrelated workflows
- panic-driven control flow

## Domain diagnostics as values

When the code needs to explain what it normalized, defaulted, or rejected, return that information as data.

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfigDiagnostic {
    UsedDefaultRetryLimit,
    ClampedContinuationBudget(u32),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WithDiagnostics<T> {
    pub value: T,
    pub diagnostics: Vec<ConfigDiagnostic>,
}

pub fn normalize_limits(input: RawLimits) -> WithDiagnostics<NormalizedLimits> {
    let retry_limit = input.retry_limit.unwrap_or(DEFAULT_RETRY_LIMIT);
    let continuation_budget = input.continuation_budget.min(MAX_CONTINUATION_BUDGET);

    let diagnostics = [
        input
            .retry_limit
            .is_none()
            .then_some(ConfigDiagnostic::UsedDefaultRetryLimit),
        (input.continuation_budget > MAX_CONTINUATION_BUDGET)
            .then_some(ConfigDiagnostic::ClampedContinuationBudget(
                input.continuation_budget,
            )),
    ]
    .into_iter()
    .flatten()
    .collect();

    WithDiagnostics {
        value: NormalizedLimits {
            retry_limit,
            continuation_budget,
        },
        diagnostics,
    }
}
```

This keeps explanation testable without forcing domain code to write logs directly.

## Boundary emission pattern

The boundary is where diagnostics become user-facing logs or output.

```rust
pub fn load_and_report_limits(
    workspace: &dyn Workspace,
    emitter: &dyn DiagnosticEmitter,
    path: &Utf8Path,
) -> Result<NormalizedLimits, LoadLimitsError> {
    read_limits_file(workspace, path).and_then(|contents| {
        parse_limits(&contents).map(|raw| {
            let normalized = normalize_limits(raw);
            normalized
                .diagnostics
                .iter()
                .for_each(|diagnostic| emitter.emit(diagnostic));
            normalized.value
        })
    })
}
```

The rule is simple:

- domain code decides which diagnostics exist
- boundary code decides how they are emitted

## Reducer failures vs handler failures

### Reducer side

Reducers should not fail from environmental causes.

Reducer logic should receive facts that already describe what happened:

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PlanEvent {
    InputsMaterialized,
    InputMaterializationFailed(PlanPreparationError),
    PlanGenerated(PlanDocument),
}
```

This keeps reducers focused on state transitions.

### Handler side

Handlers may return transport or execution errors when the effect itself could not be completed.

```rust
pub fn run_plan_generation(
    executor: &dyn PlannerExecutor,
    request: &PlanRequest,
) -> Result<PlanEvent, PlanExecutionError> {
    executor
        .run_plan(request)
        .map(PlanEvent::PlanGenerated)
        .map_err(PlanExecutionError::from)
}
```

## Prefer explicit mapping to over-generalized wrappers

When translating from one error type to another, write the mapping explicitly unless a helper actually reduces repetition.

```rust
pub fn parse_plan(contents: &str) -> Result<PlanDocument, LoadPlanError> {
    parse_xml_plan(contents).map_err(|error| match error {
        XmlPlanError::MissingRoot => LoadPlanError::InvalidPlan("missing root".to_string()),
        XmlPlanError::Malformed(message) => LoadPlanError::InvalidPlan(message),
    })
}
```

## Smells

- domain functions printing diagnostics directly
- reducers reading errors from ambient state
- stringly-typed errors where the caller needs structured behavior
- handlers swallowing error detail and returning generic success/failure booleans
- panic used where `Result` should carry the failure
