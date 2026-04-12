//! Tests for commit/mod.rs - Commit state reduction.

use crate::reducer::event::CommitEvent;
use crate::reducer::state::pipeline::{ExcludedFile, ExcludedFileReason};
use crate::reducer::state::PipelineState;
use crate::reducer::state_reduction::commit::reduce_commit_event;

fn excluded(path: &str) -> ExcludedFile {
    ExcludedFile {
        path: path.to_string(),
        reason: ExcludedFileReason::Deferred,
    }
}

#[test]
fn test_generation_started_clears_commit_excluded_files() {
    let state = PipelineState {
        commit_excluded_files: vec![excluded("src/leftover.txt")],
        ..PipelineState::initial(1, 0)
    };

    let next = reduce_commit_event(state, CommitEvent::GenerationStarted);
    assert!(
        next.commit_excluded_files.is_empty(),
        "commit_excluded_files must be cleared on commit phase reset"
    );
}

#[test]
fn test_diff_invalidated_clears_commit_excluded_files() {
    let state = PipelineState {
        commit_excluded_files: vec![excluded("src/leftover.txt")],
        ..PipelineState::initial(1, 0)
    };

    let next = reduce_commit_event(
        state,
        CommitEvent::DiffInvalidated {
            reason: "missing diff".to_string(),
        },
    );
    assert!(next.commit_excluded_files.is_empty());
}

#[test]
fn test_generation_failed_clears_retry_pass_and_excluded_files() {
    let state = PipelineState {
        commit_residual_retry_pass: 2,
        commit_excluded_files: vec![excluded("src/leftover.txt")],
        ..PipelineState::initial(1, 0)
    };

    let next = reduce_commit_event(
        state,
        CommitEvent::GenerationFailed {
            reason: "nope".to_string(),
        },
    );
    assert_eq!(next.commit_residual_retry_pass, 0);
    assert!(next.commit_excluded_files.is_empty());
}

#[test]
fn test_skipped_clears_retry_pass_and_excluded_files() {
    let state = PipelineState {
        commit_residual_retry_pass: 2,
        commit_excluded_files: vec![excluded("src/leftover.txt")],
        ..PipelineState::initial(1, 0)
    };

    let next = reduce_commit_event(
        state,
        CommitEvent::Skipped {
            reason: "skip".to_string(),
        },
    );
    assert_eq!(next.commit_residual_retry_pass, 0);
    assert!(next.commit_excluded_files.is_empty());
}

#[test]
fn test_residual_files_none_clears_excluded_files() {
    let state = PipelineState {
        commit_excluded_files: vec![excluded("src/leftover.txt")],
        ..PipelineState::initial(1, 0)
    };

    let next = reduce_commit_event(state, CommitEvent::ResidualFilesNone);
    assert!(next.commit_excluded_files.is_empty());
}

/// Proves PushExecuted does NOT double-count push_count when followed by PushCompleted.
///
/// Before fix: boundary emitted PushExecuted + PushCompleted as additional event,
/// reducer handled both, incrementing push_count twice.
///
/// After fix: reducer should handle PushExecuted only (policy interpretation happens there),
/// and PushCompleted becomes a no-op for backward compatibility.
#[test]
fn test_push_executed_does_not_double_count() {
    let initial = PipelineState {
        pending_push_commit: Some("abc123".to_string()),
        push_count: 5,
        ..PipelineState::initial(1, 0)
    };

    // Simulate boundary emitting PushExecuted (success case)
    let after_executed = reduce_commit_event(
        initial.clone(),
        CommitEvent::PushExecuted {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            commit_sha: "abc123".to_string(),
            result: crate::reducer::event::ProcessExecutionResult {
                exit_code: 0,
                stdout: String::new(),
                stderr: String::new(),
            },
        },
    );

    // PushExecuted should increment push_count and clear pending
    assert_eq!(
        after_executed.push_count, 6,
        "PushExecuted increments count"
    );
    assert_eq!(
        after_executed.pending_push_commit, None,
        "PushExecuted clears pending"
    );
    assert_eq!(
        after_executed.last_pushed_commit,
        Some("abc123".to_string()),
        "PushExecuted records SHA"
    );

    // If PushCompleted is emitted as additional event (old behavior), it should NOT
    // double-increment. Instead, it should be a no-op.
    let after_completed = reduce_commit_event(
        after_executed.clone(),
        CommitEvent::PushCompleted {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            commit_sha: "abc123".to_string(),
        },
    );

    assert_eq!(
        after_completed.push_count, 6,
        "PushCompleted must NOT increment again (no-op for compat)"
    );
    assert_eq!(
        after_completed.pending_push_commit, None,
        "state unchanged by PushCompleted"
    );
}

/// Proves PushExecuted failure path does NOT double-apply retry count.
#[test]
fn test_push_executed_failure_does_not_double_count() {
    let initial = PipelineState {
        pending_push_commit: Some("abc123".to_string()),
        push_retry_count: 1,
        ..PipelineState::initial(1, 0)
    };

    // Simulate boundary emitting PushExecuted with non-zero exit
    let after_executed = reduce_commit_event(
        initial.clone(),
        CommitEvent::PushExecuted {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            commit_sha: "abc123".to_string(),
            result: crate::reducer::event::ProcessExecutionResult {
                exit_code: 1,
                stdout: String::new(),
                stderr: "rejected".to_string(),
            },
        },
    );

    // PushExecuted should increment retry count
    assert_eq!(
        after_executed.push_retry_count, 2,
        "PushExecuted increments retry on failure"
    );
    assert_eq!(
        after_executed.last_push_error,
        Some("rejected".to_string()),
        "PushExecuted records error"
    );

    // If PushFailed is emitted as additional event (old behavior), it should NOT
    // double-increment retry count.
    let after_failed = reduce_commit_event(
        after_executed.clone(),
        CommitEvent::PushFailed {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            error: "rejected".to_string(),
        },
    );

    assert_eq!(
        after_failed.push_retry_count, 2,
        "PushFailed must NOT increment again (no-op for compat)"
    );
}

/// Proves reducer interprets exit code policy (0 = success, non-zero = failure).
#[test]
fn test_reducer_interprets_exit_code_policy() {
    let base = PipelineState {
        pending_push_commit: Some("abc123".to_string()),
        push_count: 0,
        push_retry_count: 0,
        ..PipelineState::initial(1, 0)
    };

    // Exit code 0 = success
    let success = reduce_commit_event(
        base.clone(),
        CommitEvent::PushExecuted {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            commit_sha: "abc123".to_string(),
            result: crate::reducer::event::ProcessExecutionResult {
                exit_code: 0,
                stdout: String::new(),
                stderr: String::new(),
            },
        },
    );

    assert_eq!(success.push_count, 1, "exit 0 = success, count increments");
    assert_eq!(success.pending_push_commit, None, "pending cleared");
    assert_eq!(success.push_retry_count, 0, "retry count reset");
    assert_eq!(success.last_push_error, None, "error cleared");

    // Exit code non-zero = failure
    let failure = reduce_commit_event(
        base.clone(),
        CommitEvent::PushExecuted {
            remote: "origin".to_string(),
            branch: "main".to_string(),
            commit_sha: "abc123".to_string(),
            result: crate::reducer::event::ProcessExecutionResult {
                exit_code: 1,
                stdout: String::new(),
                stderr: "auth failed".to_string(),
            },
        },
    );

    assert_eq!(
        failure.push_count, 0,
        "exit non-zero = failure, count unchanged"
    );
    assert_eq!(
        failure.pending_push_commit,
        Some("abc123".to_string()),
        "pending retained for retry"
    );
    assert_eq!(failure.push_retry_count, 1, "retry count increments");
    assert_eq!(
        failure.last_push_error,
        Some("auth failed".to_string()),
        "error recorded"
    );
}
