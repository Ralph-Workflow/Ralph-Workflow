# Testing

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **Historical Rust-era documentation** — This file describes the retired Rust implementation's testing patterns. The current Python package under `ralph-workflow/` follows different patterns. Treat this file as archival background only.

This document describes what tests should look like in this architecture.

All examples here are written to match the repository's lint expectations for tests too: no `unwrap()`, no hidden global setup, and no boundary leakage into pure tests.

## Test the layer you are changing

| Layer | Preferred test style | Test doubles |
|------|-----------------------|--------------|
| Domain helpers | Plain unit tests | None |
| Reducers | Pure state transition tests | None |
| Selectors | Plain state-query tests | None |
| Boundary functions | Integration-style tests | `MemoryWorkspace`, mock executors |
| Handlers | Integration-style event assertions | `MemoryWorkspace`, mock executors |
| System boundaries | Real end-to-end tests | Real filesystem/processes when appropriate |

## What good reducer tests look like

```rust
#[test]
fn issue_recorded_adds_issue_to_state() {
    let state = ReviewState::default();
    let issue = ReviewIssue::new("missing test");

    let next = reduce_review(state, ReviewEvent::IssueRecorded(issue.clone()));

    assert_eq!(next.issues, vec![issue]);
}
```

Reducer tests should not need mocks because reducers should not do I/O.

### Idealized reducer suite

```rust
#[test]
fn review_started_marks_state_started() {
    let state = ReviewState::default();

    let next = reduce_review(state, ReviewEvent::Started);

    assert!(next.started);
    assert!(!next.completed);
    assert!(next.issues.is_empty());
}

#[test]
fn review_completed_advances_pipeline_phase() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        review: ReviewState {
            started: true,
            completed: false,
            issues: Vec::new(),
        },
    };

    let next = reduce(state, PipelineEvent::ReviewCompleted);

    assert_eq!(next.phase, PipelinePhase::Commit);
    assert!(next.review.completed);
}
```

## What good boundary tests look like

```rust
#[test]
fn load_config_reads_workspace_then_parses_contents() {
    let workspace = MemoryWorkspace::new_test()
        .with_file("config/app.toml", "name = \"ralph\"");

    let result = load_config(&workspace, Utf8Path::new("config/app.toml"));

    assert!(result.is_ok());
}
```

Boundary tests should verify translation between external inputs and domain results.

## What good selector tests look like

```rust
#[test]
fn review_has_blockers_is_true_when_any_issue_is_blocking() {
    let state = PipelineState {
        review: ReviewState {
            started: true,
            completed: false,
            issues: vec![ReviewIssue {
                severity: Severity::Blocking,
                message: "missing schema validation".to_string(),
            }],
        },
        commit_ready: false,
    };

    assert!(review_has_blockers(&state));
}
```

Selectors should be the easiest tests in the codebase: state in, answer out.

### Idealized boundary translation test

```rust
#[test]
fn run_review_turns_executor_output_into_issue_recorded_event() {
    let executor = StubReviewExecutor::with_output(ReviewOutput {
        issue: Some(ReviewIssue {
            message: "missing regression test".to_string(),
        }),
    });
    let effect = ReviewEffect::Execute(ReviewRequest {
        prompt: "review work".to_string(),
    });

    let result = run_review(&executor, &effect);

    assert_eq!(
        result,
        Ok(ReviewEvent::IssueRecorded(ReviewIssue {
            message: "missing regression test".to_string(),
        }))
    );
}
```

## What good handler tests look like

```rust
#[test]
fn review_handler_emits_completed_event() {
    let executor = MockProcessExecutor::success("ok");
    let request = ReviewRequest::default();

    let result = execute_review(&executor, &request);

    assert!(matches!(result, Ok(ReviewEvent::Completed)));
}
```

The important assertion is the observable event, not the internal helper calls.

## Testing rules for the end state

- pure code gets pure tests
- mock only at real boundaries
- tests assert behavior and outputs, not internal implementation steps
- if a test requires serialization or global setup, that is a sign the production code still owns hidden dependencies
- validation accumulation tests should assert all expected errors, not just the first one

## Test smells

- reducer tests need a workspace or process executor
- unit tests must serialize because production code reads global state
- handler tests need to understand internal retry loops
- domain tests need temporary files just to exercise parsing or normalization

## Repository-level lint exceptions

The default rule is still: no `unwrap()`, no `.expect()`, no panic-driven assertions, and no hidden global setup.

Approved exceptions are narrow boundary cases, not an alternate testing style:

- `test-helpers` may use panic-oriented helpers because it exists to set up real `git2` / libgit2 repositories for higher-level tests.
- GUI entrypoint and framework-facing tests may keep the smallest necessary framework-shaped exception local to `ralph-gui` when Tauri or generated binding flows require it.
- Build-tooling tests in `xtask` may keep boundary-style crash behavior where the code under test is itself a top-level tooling boundary.

If a test does not fall into one of those boundary cases, fix it to match the normal style-guide rules.
