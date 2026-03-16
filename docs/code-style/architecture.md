# Architecture

This document describes the preferred finished architecture of the project.

All examples here are written to match the repository's lint policy and project architecture.

## Core flow

The target runtime shape is:

```text
State -> Orchestrator -> Effect -> Handler -> Event -> Reducer -> State
```

Each layer owns a different job.

| Layer | Role | End-state expectation |
|------|------|-----------------------|
| State | Immutable snapshot | Easy to inspect, serialize, and test |
| Orchestrator | Decide next effect | Pure, state-driven, no I/O |
| Handler | Execute one effect attempt | Thin, boundary-only, fact-producing |
| Event | Describe what happened | Past tense, data-rich, no commands |
| Reducer | Produce next state | Pure `(state, event) -> state` |

## What good reducers look like

Good reducers:

- are deterministic
- do not inspect ambient state
- do not perform logging or file access
- encode policy in explicit state transitions
- treat events as facts, not instructions

```rust
pub fn reduce_review(state: ReviewState, event: ReviewEvent) -> ReviewState {
    match event {
        ReviewEvent::Started => ReviewState {
            started: true,
            ..state
        },
        ReviewEvent::IssueRecorded(issue) => ReviewState {
            issues: state.issues.into_iter().chain([issue]).collect(),
            ..state
        },
    }
}
```

### Idealized reducer package

```rust
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PipelineState {
    pub review: ReviewState,
    pub phase: PipelinePhase,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PipelinePhase {
    Review,
    Commit,
    Done,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PipelineEvent {
    ReviewStarted,
    ReviewIssueRecorded(ReviewIssue),
    ReviewCompleted,
    CommitCompleted,
}

#[must_use]
pub fn reduce(state: PipelineState, event: PipelineEvent) -> PipelineState {
    match event {
        PipelineEvent::ReviewStarted => PipelineState {
            review: state.review.with_event(ReviewEvent::Started),
            ..state
        },
        PipelineEvent::ReviewIssueRecorded(issue) => PipelineState {
            review: state.review.with_event(ReviewEvent::IssueRecorded(issue)),
            ..state
        },
        PipelineEvent::ReviewCompleted => PipelineState {
            review: state.review.with_event(ReviewEvent::Completed),
            phase: PipelinePhase::Commit,
            ..state
        },
        PipelineEvent::CommitCompleted => PipelineState {
            phase: PipelinePhase::Done,
            ..state
        },
    }
}
```

This is the preferred relationship:

- local reducer helpers own local state transitions
- the top-level reducer composes them into pipeline transitions
- no reducer reaches into files, process state, or global config

### Idealized state, event, and effect vocabulary

The project should model runtime progression with small, explicit types.

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PipelinePhase {
    Planning,
    Development,
    Review,
    Commit,
    Done,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Effect {
    Plan(PlanRequest),
    Develop(DevelopmentRequest),
    Review(ReviewRequest),
    Commit(CommitRequest),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Event {
    PlanningStarted,
    PlanGenerated(PlanDocument),
    DevelopmentCompleted,
    ReviewCompleted,
    CommitCompleted,
}
```

The naming rule is important:

- effects describe work to execute
- events describe facts that already happened
- state carries the policy-relevant memory between them

## What good handlers look like

Good handlers:

- perform one concrete effect attempt
- translate runtime results into fact-shaped events
- do not hide retry loops or fallback logic
- do not mutate pipeline state directly

```rust
pub fn execute_review(
    executor: &dyn ProcessExecutor,
    request: &ReviewRequest,
) -> Result<ReviewEvent, ReviewExecutionError> {
    run_review_process(executor, request)
        .map(ReviewEvent::Completed)
        .map_err(ReviewExecutionError::from)
}
```

### Idealized handler/result translation

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReviewRequest {
    pub prompt: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReviewOutput {
    pub issue: Option<ReviewIssue>,
}

pub trait ReviewExecutor {
    fn run_review(&self, request: &ReviewRequest) -> Result<ReviewOutput, ReviewExecutionError>;
}

pub fn execute_review(
    executor: &dyn ReviewExecutor,
    request: &ReviewRequest,
) -> Result<PipelineEvent, ReviewExecutionError> {
    executor.run_review(request).map(|output| {
        output.issue.map_or(PipelineEvent::ReviewCompleted, |issue| {
            PipelineEvent::ReviewIssueRecorded(issue)
        })
    })
}
```

This is the preferred shape for handlers in the project:

- accept all inputs explicitly
- execute one attempt
- translate runtime output into a fact-shaped event
- return

## What good events look like

Events should read like things that already happened:

- `ReviewStarted`
- `ReviewCompleted`
- `ValidationFailed`
- `CommitSkipped`

Events should not read like commands:

- `RetryReview`
- `WriteCommit`
- `AdvancePhase`

Reducers decide those consequences from state plus facts.

### Event payloads should carry decision inputs

Good events bring enough data for reducers to make the next policy decision without rereading the world.

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReviewEvent {
    Started,
    IssueRecorded(ReviewIssue),
    Completed {
        issue_count: usize,
        summary: String,
    },
}
```

This is better than an event that only says "review happened" while forcing reducers to inspect logs or external files.

## What good orchestration looks like

Orchestration code should be pure policy code:

- inspect state
- decide next effect
- stop

```rust
pub fn determine_next_effect(state: &PipelineState) -> Option<Effect> {
    match state.phase {
        PipelinePhase::Planning if state.plan.is_none() => Some(Effect::Plan),
        PipelinePhase::Review if state.pending_review => Some(Effect::Review),
        _ => None,
    }
}
```

### Idealized orchestration example

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Effect {
    Review(ReviewRequest),
    Commit,
}

#[must_use]
pub fn determine_next_effect(state: &PipelineState) -> Option<Effect> {
    match state.phase {
        PipelinePhase::Review if !state.review.started => Some(Effect::Review(ReviewRequest {
            prompt: "review current work".to_string(),
        })),
        PipelinePhase::Commit if state.review.completed => Some(Effect::Commit),
        _ => None,
    }
}
```

The orchestration layer should be boring in the best possible way: straightforward state inspection followed by an explicit effect decision.

### Selector pattern

Selectors should stay pure and narrow too.

```rust
#[must_use]
pub fn current_issue_count(state: &PipelineState) -> usize {
    state.review.issues.len()
}

#[must_use]
pub fn is_ready_for_commit(state: &PipelineState) -> bool {
    matches!(state.phase, PipelinePhase::Commit) && state.review.completed
}
```

Selectors should compute answers from state, not trigger work.

## Architecture mistakes to avoid

- reducers that read files or environment variables
- handlers that contain retry loops
- events that encode policy instead of facts
- orchestration logic hidden inside boundary helpers
- UI or logging output that changes correctness decisions
