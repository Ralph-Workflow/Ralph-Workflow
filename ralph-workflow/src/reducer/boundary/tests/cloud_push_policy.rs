//! Tests proving cloud push boundary returns domain-shaped outcomes without
//! interpreting exit-code policy.
//!
//! TDD red-first tests for P4-policy-cloud-exitcode-triway fix.

use super::common::TestFixture;
use crate::executor::ProcessOutput;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::event::{CommitEvent, PipelineEvent};
use std::os::unix::process::ExitStatusExt;
use std::process::ExitStatus;

/// Proves boundary returns PushExecuted with raw ProcessOutput, not PushCompleted.
///
/// Boundary should NOT interpret exit code 0 as "success" - that's reducer policy.
#[test]
fn test_push_boundary_returns_executed_not_completed() {
    let mut fixture = TestFixture::new();
    let ctx = fixture.ctx();

    // Boundary executes push
    let result = MainEffectHandler::handle_push_to_remote(
        &ctx,
        "origin".to_string(),
        "main".to_string(),
        false,
        "abc123".to_string(),
    );

    // Boundary returns PushExecuted with raw output, NOT PushCompleted/PushFailed
    match result.event {
        PipelineEvent::Commit(CommitEvent::PushExecuted {
            remote,
            branch,
            commit_sha,
            result,
        }) => {
            assert_eq!(remote, "origin");
            assert_eq!(branch, "main");
            assert_eq!(commit_sha, "abc123");
            // Result structure is present (exit_code + stdout/stderr) - reducer interprets it
            assert_eq!(result.exit_code, 0, "default mock returns success");
        }
        other => panic!("expected PushExecuted, got {:?}", other),
    }
}

/// Proves boundary does not branch on exit code - returns same event variant regardless.
#[test]
fn test_push_boundary_no_exitcode_branching() {
    let mut fixture = TestFixture::new();

    // Configure mock executor to return non-zero exit (push rejection scenario)
    let failure_output = ProcessOutput {
        status: ExitStatus::from_raw(256), // exit code 1 (shifted)
        stdout: String::new(),
        stderr: "rejected".to_string(),
    };

    fixture.executor = std::sync::Arc::new(
        crate::executor::MockProcessExecutor::new().with_result("git", Ok(failure_output.clone())),
    );

    let ctx = fixture.ctx();
    let result = MainEffectHandler::handle_push_to_remote(
        &ctx,
        "origin".to_string(),
        "feature".to_string(),
        false,
        "def456".to_string(),
    );

    // Boundary returns PushExecuted regardless of exit code
    match result.event {
        PipelineEvent::Commit(CommitEvent::PushExecuted { result, .. }) => {
            assert_ne!(result.exit_code, 0, "boundary preserves failure exit code");
            assert!(
                result.stderr.contains("rejected"),
                "boundary preserves stderr"
            );
        }
        other => panic!(
            "expected PushExecuted even on failure exit, got {:?}",
            other
        ),
    }
}
