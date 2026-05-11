# Coding Patterns

> **Historical Rust-era documentation** — This file describes the retired Rust implementation's coding patterns. The current Python package under `ralph-workflow/` follows different patterns. Treat this file as archival background only.

This document shows the preferred coding patterns for ordinary project Rust code.

All code examples here are written to be compatible with the repository's lint and dylint expectations for non-boundary code.

## Iterator pipeline for collection building

```rust
let summaries: Vec<_> = tasks
    .into_iter()
    .filter(|task| task.is_ready())
    .map(Task::into_summary)
    .collect();
```

Use this when you are filtering, mapping, flattening, collecting, counting, or summing.

## Fold for state evolution

```rust
let summary = events.into_iter().fold(Summary::default(), Summary::with_event);
```

This is the preferred reducer-shaped accumulation pattern.

## `?` for typed failure propagation

```rust
fn process(request: Request) -> Result<Response, Error> {
    let validated = validate(&request)?;
    let authorized = authorize(&validated)?;
    execute(&authorized)
}
```

Use `?` by default when each step produces the next typed input.

## Explicit `match` when it improves clarity

```rust
fn classify_review(issue_count: usize) -> ReviewOutcome {
    match issue_count {
        0 => ReviewOutcome::Approved,
        1..=3 => ReviewOutcome::ChangesRequested,
        _ => ReviewOutcome::Escalated,
    }
}
```

Prefer explicit `match` over clever combinator chains when the branch structure is the point.

## Type-driven domain modeling

```rust
pub struct UserId(i64);
pub struct Email(String);

fn lookup_user(user_id: UserId) -> Option<User> { /* ... */ }
fn send_email(address: Email) -> Result<(), Error> { /* ... */ }
```

## Logging as data, then emitted later

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
enum ValidationLog {
    UsingDefaultTimeout,
    ClampedRetryLimit(u32),
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Logged<T> {
    value: T,
    logs: Vec<ValidationLog>,
}

fn normalize_settings(input: RawSettings) -> Logged<Settings> {
    let timeout = input.timeout.unwrap_or(DEFAULT_TIMEOUT_MS);
    let retry_limit = input.retry_limit.min(MAX_RETRY_LIMIT);

    let logs = [
        input
            .timeout
            .is_none()
            .then_some(ValidationLog::UsingDefaultTimeout),
        (input.retry_limit > MAX_RETRY_LIMIT)
            .then_some(ValidationLog::ClampedRetryLimit(input.retry_limit)),
    ]
    .into_iter()
    .flatten()
    .collect();

    Logged {
        value: Settings {
            timeout,
            retry_limit,
        },
        logs,
    }
}
```

Use newtypes when primitive values have stable business meaning.

## Resolve dependencies before pure code runs

```rust
struct RetryPolicyInput {
    max_retries: u32,
}

fn build_retry_policy(input: RetryPolicyInput) -> RetryPolicy {
    RetryPolicy {
        max_retries: input.max_retries,
    }
}
```

In the end state, environment access happens in `runtime/` or `boundary/`, never inside ordinary domain helpers.

## Reducer composition pattern

```rust
#[derive(Debug, Clone, PartialEq, Eq, Default)]
struct PipelineState {
    review: ReviewState,
    commit_ready: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum PipelineEvent {
    ReviewStarted,
    ReviewCompleted,
}

fn reduce(state: PipelineState, event: PipelineEvent) -> PipelineState {
    match event {
        PipelineEvent::ReviewStarted => PipelineState {
            review: state.review.with_event(ReviewEvent::Started),
            ..state
        },
        PipelineEvent::ReviewCompleted => PipelineState {
            review: state.review.with_event(ReviewEvent::Completed),
            commit_ready: true,
            ..state
        },
    }
}
```

This is the target style for project reducer code: compose smaller state transitions into a larger immutable transition.

## Selector pattern

```rust
fn review_has_blockers(state: &PipelineState) -> bool {
    state
        .review
        .issues
        .iter()
        .any(|issue| issue.severity == Severity::Blocking)
}
```

Selectors should derive facts from state and return plain values.

## Effect selection pattern

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
enum Effect {
    Review(ReviewRequest),
    Commit,
}

fn determine_next_effect(state: &PipelineState) -> Option<Effect> {
    (!state.review.started)
        .then(|| {
            Effect::Review(ReviewRequest {
                prompt: "review current work".to_string(),
            })
        })
        .or_else(|| state.commit_ready.then_some(Effect::Commit))
}
```

This is the target style for orchestration: read state, derive effect, stop.

## Input object instead of long parameter list

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
struct CommitRequest {
    message: String,
    author_name: String,
    author_email: String,
}

fn build_commit_command(input: CommitRequest) -> CommandSpec {
    CommandSpec {
        program: "git".to_string(),
        args: vec![
            "commit".to_string(),
            "-m".to_string(),
            input.message,
            "--author".to_string(),
            format!("{} <{}>", input.author_name, input.author_email),
        ],
    }
}
```

Prefer explicit input structs over a long list of related parameters.

## Prefer explicit enums over fancy unions

```rust
enum Response {
    Success(SuccessData),
    Redirect(RedirectData),
    Error(ErrorData),
}

fn render_response(response: Response) -> String {
    match response {
        Response::Success(data) => format!("ok:{}", data.body),
        Response::Redirect(data) => format!("redirect:{}", data.location),
        Response::Error(data) => format!("error:{}", data.code),
    }
}
```

## Use advanced abstractions only when they earn it

Good reasons:

- repeated compatible conversions
- true error accumulation
- real generic duplication across multiple sites

Bad reasons:

- avoiding a short explicit mapping
- hiding simple domain shapes behind generic machinery
- making simple code look more academically functional
