//! Integration tests for `OpenCode` usage limit detection.
//!
//! Verifies end-to-end `OpenCode` usage limit detection flow:
//! 1. `OpenCode` emits JSON error to logfile with `usage_limit_exceeded` code
//! 2. Error extraction from logfile identifies the error
//! 3. Error classification detects it as `RateLimit` (not `InternalError`)
//! 4. `RateLimit` event triggers agent fallback
//!
//! Observable behaviors tested:
//! - Structured error codes (`insufficient_quota`, `usage_limit_exceeded`, `quota_exceeded`)
//! - Provider-specific errors (anthropic: usage limit, openai: usage limit)
//! - `OpenCode` Zen branded errors (opencode usage limit, zen usage limit)
//! - `RateLimit` event emission (not `InternalError`)
//! - Agent fallback triggered after `RateLimit` detection
//!
//! # Integration Test Compliance
//!
//! These tests follow [../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md):
//! - Test observable behavior: error detection and agent fallback
//! - Mock at architectural boundaries: `MemoryWorkspace` for filesystem
//! - Pure reducer tests require no mocks

use crate::test_timeout::with_default_timeout;
use ralph_workflow::pipeline::extract_error_identifier_from_logfile;
use ralph_workflow::reducer::event::AgentErrorKind;
use ralph_workflow::reducer::fault_tolerant_executor::classify_agent_error;
use ralph_workflow::workspace::{MemoryWorkspace, Workspace};
use std::path::Path;

/// Test that `OpenCode` structured error code `usage_limit_exceeded` is correctly extracted and classified.
///
/// This is the PRIMARY error format from `OpenCode` when usage limits are hit.
/// The error code is more reliable than message text for detection.
#[test]
fn test_opencode_structured_code_usage_limit_exceeded() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        // Mock OpenCode logfile with structured error code
        let logfile_content = r#"{"type":"content","content":"Analyzing..."}
{"type":"error","error":{"code":"usage_limit_exceeded"}}"#;

        workspace
            .write(Path::new(".agent/logs/agent1.log"), logfile_content)
            .unwrap();

        // Extract error from logfile
        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert!(
            error_msg.is_some(),
            "Should extract error from OpenCode JSON"
        );
        assert_eq!(
            error_msg.as_deref(),
            Some("usage_limit_exceeded"),
            "Should extract error code"
        );

        // Classify error - should be RateLimit, not InternalError
        let error_kind = classify_agent_error(1, "", error_msg.as_deref());

        assert!(
            matches!(error_kind, AgentErrorKind::RateLimit),
            "usage_limit_exceeded should be classified as RateLimit, got {error_kind:?}"
        );
    });
}

/// Test silent failure: `OpenCode` exits with non-zero code but no error logs.
///
/// Simulates worst-case scenario where `OpenCode` usage limit causes immediate exit
/// without writing to logfile or stderr. This can happen if:
/// - Provider API returns 429 and `OpenCode` exits before writing error event
/// - Network interruption prevents error log from being written
/// - Process killed during error emission
///
/// **Expected behavior**: Without error signals, this cannot be detected as a usage
/// limit error and will be classified as `InternalError`. This test documents the
/// current limitation. Future improvements may add heuristics or debug logging.
#[test]
fn test_silent_usage_limit_failure() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        // Scenario 1: Empty logfile (OpenCode exited before writing anything)
        workspace
            .write(Path::new(".agent/logs/agent1.log"), "")
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert!(error_msg.is_none(), "Empty logfile should return None");

        // Classify with exit code 1 and empty stderr
        // Current behavior: Will be InternalError without additional signals
        let error_kind = classify_agent_error(1, "", None);

        // Document current limitation: Cannot detect usage limit without error signals
        assert!(
            matches!(error_kind, AgentErrorKind::InternalError),
            "Without error signals, classified as InternalError (expected limitation), got {error_kind:?}"
        );

        // Scenario 2: Missing logfile (OpenCode exited immediately)
        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/nonexistent.log",
            &workspace as &dyn Workspace,
        );

        assert!(error_msg.is_none(), "Missing logfile should return None");

        let error_kind = classify_agent_error(1, "", None);
        assert!(
            matches!(error_kind, AgentErrorKind::InternalError),
            "Missing logfile should be classified as InternalError, got {error_kind:?}"
        );
    });
}

/// Test that specific exit codes alone do not indicate usage limits.
///
/// `OpenCode` uses generic exit code 1 for all errors, so exit codes alone
/// cannot reliably detect usage limits. This test documents that exit code
/// detection is NOT implemented because it would cause false positives.
#[test]
fn test_exit_code_alone_insufficient_for_detection() {
    with_default_timeout(|| {
        // Test various exit codes with no error messages
        // None should be classified as RateLimit without additional context
        let test_cases = vec![
            1,   // Generic error
            2,   // Command line usage error
            3,   // Could indicate resource exhaustion
            75,  // EX_TEMPFAIL (temporary failure)
            127, // Command not found
        ];

        for exit_code in test_cases {
            let error_kind = classify_agent_error(exit_code, "", None);
            assert!(
                !matches!(error_kind, AgentErrorKind::RateLimit),
                "Exit code {exit_code} alone should NOT be classified as RateLimit (prevents false positives), got {error_kind:?}"
            );
        }
    });
}

/// Test detection when `OpenCode` writes partial log before crashing.
///
/// If `OpenCode` crashes after writing some content but before the error event,
/// we should not detect a usage limit error (no false positives).
#[test]
fn test_partial_log_no_error_event() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        // Partial log: OpenCode wrote some content but crashed before error event
        let logfile_content = r#"{"type":"content","content":"Analyzing codebase..."}
{"type":"tool_use","tool":"read","state":{"status":"running"}}"#;

        workspace
            .write(Path::new(".agent/logs/agent1.log"), logfile_content)
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        // No error event in log
        assert!(
            error_msg.is_none(),
            "Partial log without error event should return None"
        );

        let error_kind = classify_agent_error(1, "", None);

        // Should not be classified as RateLimit without error signal
        assert!(
            !matches!(error_kind, AgentErrorKind::RateLimit),
            "Partial log without error should not be RateLimit, got {error_kind:?}"
        );
    });
}

/// Test detection via stderr when logfile is unavailable.
///
/// If logfile extraction fails but stderr contains usage limit error,
/// detection should still work (fallback to stderr).
#[test]
fn test_detection_via_stderr_when_logfile_unavailable() {
    with_default_timeout(|| {
        // Logfile missing, but stderr has usage limit error
        let stderr = "Error: OpenCode usage limit has been reached";
        let error_kind = classify_agent_error(1, stderr, None);

        assert!(
            matches!(error_kind, AgentErrorKind::RateLimit),
            "Should detect usage limit from stderr when logfile unavailable, got {error_kind:?}"
        );
    });
}

/// Test that both stderr and stdout sources are checked for rate limit errors.
///
/// When both streams contain error information, both should be checked and
/// rate limit detected if present in either. Note that timeout errors in stderr
/// will be classified as Timeout (higher priority) even if stdout has rate limit.
#[test]
fn test_dual_source_error_detection() {
    with_default_timeout(|| {
        // Test 1: Usage limit in stdout, generic error in stderr
        let stderr = "network connection failed";
        let stdout_error = Some("usage_limit_exceeded");
        let error_kind = classify_agent_error(1, stderr, stdout_error);

        assert!(
            matches!(error_kind, AgentErrorKind::RateLimit),
            "Should detect usage limit from stdout even with stderr error, got {error_kind:?}"
        );

        // Test 2: Usage limit in stderr, other error in stdout
        let stderr = "anthropic: usage limit reached";
        let stdout_error = Some("connection failed");
        let error_kind = classify_agent_error(1, stderr, stdout_error);

        assert!(
            matches!(error_kind, AgentErrorKind::RateLimit),
            "Should detect usage limit from stderr even with stdout error, got {error_kind:?}"
        );
    });
}

/// Test that `OpenCode` error code `quota_exceeded` is correctly detected.
///
/// Generic quota exhaustion code used by multiple providers.
#[test]
fn test_opencode_quota_exceeded_code() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        let logfile_content = r#"{"type":"error","error":{"code":"quota_exceeded"}}"#;

        workspace
            .write(Path::new(".agent/logs/agent1.log"), logfile_content)
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert_eq!(error_msg.as_deref(), Some("quota_exceeded"));

        let error_kind = classify_agent_error(1, "", error_msg.as_deref());

        assert!(
            matches!(error_kind, AgentErrorKind::RateLimit),
            "quota_exceeded should be classified as RateLimit, got {error_kind:?}"
        );
    });
}

/// Test provider-specific error: `anthropic: usage limit reached`.
///
/// `OpenCode` multi-provider gateway forwards provider errors with provider context.
/// Format: `{"error": {"provider": "anthropic", "message": "usage limit reached"}}`
#[test]
fn test_opencode_provider_specific_anthropic_usage_limit() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        let logfile_content =
            r#"{"type":"error","error":{"provider":"anthropic","message":"usage limit reached"}}"#;

        workspace
            .write(Path::new(".agent/logs/agent1.log"), logfile_content)
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert!(
            error_msg.is_some(),
            "Should extract provider-specific error"
        );
        // Should get "anthropic: usage limit reached" format
        assert!(
            error_msg.as_deref().unwrap().contains("anthropic"),
            "Should preserve provider context: {}",
            error_msg.as_deref().unwrap()
        );
        assert!(
            error_msg.as_deref().unwrap().contains("usage limit"),
            "Should contain usage limit message: {}",
            error_msg.as_deref().unwrap()
        );

        let error_kind = classify_agent_error(1, "", error_msg.as_deref());

        assert!(
            matches!(error_kind, AgentErrorKind::RateLimit),
            "Provider-specific usage limit should be classified as RateLimit, got {error_kind:?}"
        );
    });
}

/// Test provider-specific error: `openai: usage limit exceeded`.
///
/// `OpenAI` provider emits usage limit errors through `OpenCode` multi-provider gateway.
#[test]
fn test_opencode_provider_specific_openai_usage_limit() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        let logfile_content =
            r#"{"type":"error","error":{"provider":"openai","message":"usage limit exceeded"}}"#;

        workspace
            .write(Path::new(".agent/logs/agent1.log"), logfile_content)
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert!(error_msg.is_some());
        assert!(error_msg.as_deref().unwrap().contains("openai"));
        assert!(error_msg.as_deref().unwrap().contains("usage limit"));

        let error_kind = classify_agent_error(1, "", error_msg.as_deref());

        assert!(
            matches!(error_kind, AgentErrorKind::RateLimit),
            "OpenAI usage limit should be classified as RateLimit, got {error_kind:?}"
        );
    });
}

/// Test `OpenCode` Zen branded error: `OpenCode Zen usage limit has been reached`.
///
/// `OpenCode` Zen (paid tier) emits branded usage limit messages.
#[test]
fn test_opencode_zen_usage_limit_message() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        let logfile_content =
            r#"{"type":"error","error":{"message":"OpenCode Zen usage limit has been reached"}}"#;

        workspace
            .write(Path::new(".agent/logs/agent1.log"), logfile_content)
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert_eq!(
            error_msg.as_deref(),
            Some("OpenCode Zen usage limit has been reached")
        );

        let error_kind = classify_agent_error(1, "", error_msg.as_deref());

        assert!(
            matches!(error_kind, AgentErrorKind::RateLimit),
            "OpenCode Zen usage limit should be classified as RateLimit, got {error_kind:?}"
        );
    });
}

/// Test `OpenCode` usage limit message: `opencode usage limit reached`.
///
/// Generic `OpenCode` usage limit message (non-Zen).
#[test]
fn test_opencode_usage_limit_message() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        let logfile_content =
            r#"{"type":"error","error":{"message":"opencode usage limit reached"}}"#;

        workspace
            .write(Path::new(".agent/logs/agent1.log"), logfile_content)
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert_eq!(error_msg.as_deref(), Some("opencode usage limit reached"));

        let error_kind = classify_agent_error(1, "", error_msg.as_deref());

        assert!(
            matches!(error_kind, AgentErrorKind::RateLimit),
            "OpenCode usage limit should be classified as RateLimit, got {error_kind:?}"
        );
    });
}

/// Test that generic "usage limit reached" message is detected.
///
/// Generic message pattern that may be emitted by various providers.
#[test]
fn test_generic_usage_limit_reached_message() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        let logfile_content = r#"{"type":"error","error":{"message":"usage limit reached"}}"#;

        workspace
            .write(Path::new(".agent/logs/agent1.log"), logfile_content)
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert_eq!(error_msg.as_deref(), Some("usage limit reached"));

        let error_kind = classify_agent_error(1, "", error_msg.as_deref());

        assert!(
            matches!(error_kind, AgentErrorKind::RateLimit),
            "Generic usage limit should be classified as RateLimit, got {error_kind:?}"
        );
    });
}

/// Test that "usage limit has been reached" message is detected.
///
/// Longer form of the usage limit message.
#[test]
fn test_usage_limit_has_been_reached_message() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        let logfile_content =
            r#"{"type":"error","error":{"message":"usage limit has been reached"}}"#;

        workspace
            .write(Path::new(".agent/logs/agent1.log"), logfile_content)
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert_eq!(error_msg.as_deref(), Some("usage limit has been reached"));

        let error_kind = classify_agent_error(1, "", error_msg.as_deref());

        assert!(
            matches!(error_kind, AgentErrorKind::RateLimit),
            "usage limit has been reached should be classified as RateLimit, got {error_kind:?}"
        );
    });
}

/// Test that errors are extracted from the last 50 lines of logfile.
///
/// `OpenCode` may emit multiple events to the same logfile.
/// Error extraction should search recent lines (last 50) in reverse order.
#[test]
fn test_error_extraction_searches_last_50_lines() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        // Create logfile with 100 lines, error at line 80
        let mut lines = Vec::new();
        for i in 1..=79 {
            lines.push(format!(r#"{{"type":"content","content":"Line {i}"}}"#));
        }
        lines.push(r#"{"type":"error","error":{"code":"usage_limit_exceeded"}}"#.to_string());
        for i in 81..=100 {
            lines.push(format!(r#"{{"type":"content","content":"Line {i}"}}"#));
        }

        let logfile_content = lines.join("\n");

        workspace
            .write(Path::new(".agent/logs/agent1.log"), &logfile_content)
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert!(
            error_msg.is_some(),
            "Should find error within last 50 lines (line 80 of 100)"
        );
        assert_eq!(error_msg.as_deref(), Some("usage_limit_exceeded"));
    });
}

/// Test that non-error JSON events are ignored.
///
/// `OpenCode` emits multiple event types (content, thinking, etc.).
/// Only "error" type events should be extracted.
#[test]
fn test_non_error_events_ignored() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        let logfile_content = r#"{"type":"content","content":"Hello"}
{"type":"thinking","thinking":"Analyzing..."}
{"type":"tool_use","name":"read_file"}
{"type":"completion","finish_reason":"stop"}"#;

        workspace
            .write(Path::new(".agent/logs/agent1.log"), logfile_content)
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert!(
            error_msg.is_none(),
            "Should not extract error from non-error events"
        );
    });
}

/// Test that malformed JSON lines are skipped.
///
/// Logfiles may contain partial or malformed JSON.
/// Error extraction should skip invalid lines and continue searching.
#[test]
fn test_malformed_json_skipped() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        let logfile_content = r#"{"type":"content","content":"Valid"}
{invalid json line
{"type":"error","error":{"code":"usage_limit_exceeded"}}"#;

        workspace
            .write(Path::new(".agent/logs/agent1.log"), logfile_content)
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert!(
            error_msg.is_some(),
            "Should skip malformed JSON and find valid error"
        );
        assert_eq!(error_msg.as_deref(), Some("usage_limit_exceeded"));
    });
}

/// Test that empty logfile returns None.
#[test]
fn test_empty_logfile_returns_none() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        workspace
            .write(Path::new(".agent/logs/agent1.log"), "")
            .unwrap();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/agent1.log",
            &workspace as &dyn Workspace,
        );

        assert!(error_msg.is_none(), "Empty logfile should return None");
    });
}

/// Test that missing logfile returns None.
#[test]
fn test_missing_logfile_returns_none() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();

        let error_msg = extract_error_identifier_from_logfile(
            ".agent/logs/nonexistent.log",
            &workspace as &dyn Workspace,
        );

        assert!(error_msg.is_none(), "Missing logfile should return None");
    });
}

/// Test full pipeline: `OpenCode` usage limit → agent fallback via reducer.
///
/// This test verifies the complete flow:
/// 1. Agent invocation fails with usage limit error
/// 2. Error is classified as `RateLimit`
/// 3. Reducer emits `RateLimit` event
/// 4. Agent chain advances to next agent
#[test]
fn test_full_usage_limit_triggers_agent_fallback() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;
        use ralph_workflow::reducer::event::PipelineEvent;
        use ralph_workflow::reducer::state::{AgentChainState, PipelineState};
        use ralph_workflow::reducer::state_reduction::reduce;

        // Setup state with two agents
        let state = PipelineState {
            phase: ralph_workflow::reducer::event::PipelinePhase::Development,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["opencode-claude".to_string(), "opencode-codex".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            ),
            ..crate::common::with_locked_prompt_permissions(PipelineState::initial(5, 2))
        };

        assert_eq!(
            state.agent_chain.current_agent().unwrap(),
            "opencode-claude",
            "Should start with first agent"
        );

        // Simulate usage limit error via RateLimit event
        let new_state = reduce(
            state,
            PipelineEvent::agent_rate_limited(
                AgentRole::Developer,
                "opencode-claude".into(),
                Some("continue work".to_string()),
            ),
        );

        // Should switch to next agent
        assert_eq!(
            new_state.agent_chain.current_agent().unwrap(),
            "opencode-codex",
            "RateLimit should trigger agent fallback"
        );

        // Should preserve continuation prompt
        assert!(
            new_state
                .agent_chain
                .rate_limit_continuation_prompt
                .is_some(),
            "Should preserve prompt for continuation"
        );
    });
}

/// Test that usage limit detection works with stderr fallback.
///
/// If `OpenCode` error is not in logfile JSON, it may be in stderr.
/// Error classification should detect usage limit patterns in stderr.
#[test]
fn test_usage_limit_detection_in_stderr() {
    with_default_timeout(|| {
        // Test stderr patterns
        let test_cases = vec![
            ("anthropic: usage limit reached", AgentErrorKind::RateLimit),
            ("openai: usage limit exceeded", AgentErrorKind::RateLimit),
            (
                "zen usage limit has been reached",
                AgentErrorKind::RateLimit,
            ),
            ("opencode usage limit reached", AgentErrorKind::RateLimit),
            ("usage limit has been reached", AgentErrorKind::RateLimit),
            ("usage limit reached", AgentErrorKind::RateLimit),
            ("usage limit exceeded", AgentErrorKind::RateLimit),
        ];

        for (stderr, expected_kind) in test_cases {
            let error_kind = classify_agent_error(1, stderr, None);

            assert!(
                matches!(error_kind, k if k == expected_kind),
                "stderr '{stderr}' should be classified as {expected_kind:?}, got {error_kind:?}"
            );
        }
    });
}

/// Test that non-usage-limit errors are NOT classified as `RateLimit`.
///
/// Ensure other error types are correctly distinguished from rate limit errors.
#[test]
fn test_non_usage_limit_errors_not_classified_as_rate_limit() {
    with_default_timeout(|| {
        // Test various error messages that should NOT be classified as RateLimit
        let non_rate_limit_errors = vec![
            "network connection failed",
            "authentication failed",
            "internal server error",
            "timed out",
            "file not found",
            "parse error",
            "model not found",
        ];

        for stderr in non_rate_limit_errors {
            let error_kind = classify_agent_error(1, stderr, None);

            assert!(
                !matches!(error_kind, AgentErrorKind::RateLimit),
                "stderr '{stderr}' should NOT be RateLimit, got {error_kind:?}"
            );
        }
    });
}
