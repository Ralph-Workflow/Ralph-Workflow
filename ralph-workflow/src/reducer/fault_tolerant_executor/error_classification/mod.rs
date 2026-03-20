//! Error classification for fault-tolerant agent execution.
//!
//! This module provides functions to classify errors from agent execution
//! into categories that determine retry vs fallback behavior.
//!
//! # `OpenCode` Usage Limit Detection
//!
//! `OpenCode` and multi-provider gateways emit usage limit errors when underlying
//! providers (`OpenAI`, Anthropic, Google, etc.) hit quota/usage limits. This module
//! provides comprehensive detection for all `OpenCode` error formats:
//!
//! ## Detection Methods (Priority Order)
//!
//! 1. **Structured error codes** (JSON in stdout logfile):
//!    - Primary: `usage_limit_exceeded`, `quota_exceeded`, `insufficient_quota`
//!    - Extracted via `extract_error_identifier_from_logfile()`
//!    - Most reliable when available
//!
//! 2. **Message patterns** (stderr or extracted from JSON):
//!    - "usage limit has been reached", "usage limit reached", "usage limit exceeded"
//!    - "`OpenCode` Zen usage limit", "opencode usage limit"
//!    - Provider-prefixed: "anthropic: usage limit reached"
//!
//! 3. **Exit codes**: `OpenCode` uses generic exit code 1 for most errors.
//!    - Exit codes are NOT reliable for usage limit detection
//!    - No specific exit codes for rate limits vs other errors
//!    - Exit code-based detection NOT implemented to avoid false positives
//!
//! ## Error Emission Behavior
//!
//! Based on `OpenCode` source analysis (2026-02-12):
//!
//! - **Primary channel**: JSON events to stdout via `--format json` logfile
//!   - Format: `{"type":"error","error":{"code":"usage_limit_exceeded"}}`
//!   - Written to `.agent/logs/*.log`
//!
//! - **Secondary channel**: stderr for some error messages
//!   - Used as fallback when logfile unavailable
//!
//! - **Exit codes**: Generic (exit code 1 for all errors)
//!   - Source: `/packages/opencode/src/cli/cmd/run.ts`
//!   - Cannot distinguish usage limits from other errors
//!
//! - **Retry logic**: `/packages/opencode/src/session/retry.ts`
//!   - Checks for `FreeUsageLimitError` in response body
//!   - Checks for `json.error?.code?.includes("rate_limit")`
//!
//! ## Edge Cases and Limitations
//!
//! - **Silent failures**: If `OpenCode` exits before writing error logs, detection
//!   may fall back to logging a warning about potential undetected usage limits
//! - **Empty logfiles**: Can occur if process terminates during initialization
//! - **Timing issues**: Error may appear in stderr but not in logfile if process
//!   exits before flushing stdout buffer
//! - **No heuristics**: Exit code-based detection NOT implemented because `OpenCode`
//!   uses exit code 1 for all errors (would cause false positives)
//!
//! ## Verification and Maintenance
//!
//! Last Verified: 2026-02-12
//! Last Updated: 2026-02-12
//!
//! To verify patterns remain accurate as `OpenCode` evolves:
//! 1. Check `OpenCode` source: <https://github.com/anomalyco/opencode>
//! 2. Review `/packages/opencode/src/cli/cmd/run.ts` for error emission
//! 3. Review `/packages/opencode/src/session/message-v2.ts` for error formats
//! 4. Review `/packages/opencode/src/session/retry.ts` for retry logic
//! 5. Test with `OpenCode` CLI near usage limit to observe actual messages
//! 6. Update patterns in this file if format changes

mod rate_limit;

use crate::reducer::event::AgentErrorKind;

/// Classify agent error from exit code, stderr, and optional stdout content.
///
/// # Arguments
///
/// * `exit_code` - Process exit code
/// * `stderr` - Standard error output
/// * `stdout_error` - Optional error message extracted from stdout (e.g., from JSON logs)
///
/// # Stdout Error Detection
///
/// Some agents (like `OpenCode`) emit errors as JSON to stdout rather than stderr.
/// When `stdout_error` is provided, it is examined for rate limit patterns alongside stderr.
/// This ensures rate limit errors are properly detected regardless of output stream.
#[must_use]
pub fn classify_agent_error(
    exit_code: i32,
    stderr: &str,
    stdout_error: Option<&str>,
) -> AgentErrorKind {
    const SIGSEGV: i32 = 139;
    const SIGABRT: i32 = 134;
    const SIGTERM: i32 = 143;

    match exit_code {
        SIGSEGV | SIGABRT => AgentErrorKind::InternalError,
        SIGTERM => AgentErrorKind::Timeout,
        _ => {
            let stderr_lower = stderr.to_lowercase();

            if is_timeout_stderr(&stderr_lower) {
                AgentErrorKind::Timeout
            } else if rate_limit::is_rate_limit_error_from_any_source(
                &stderr_lower,
                stderr,
                stdout_error,
            ) {
                // Rate limit detection must run before broad auth heuristics.
                // Some providers encode quota/rate-limit as 403 Forbidden, and we
                // still want the "429 => rate-limit policy" semantics.
                AgentErrorKind::RateLimit
            } else if stderr_lower.contains("unauthorized")
                || stderr_lower.contains("authentication")
                || stderr_lower.contains("401")
                || stderr_lower.contains("api key")
                || stderr_lower.contains("invalid token")
                || stderr_lower.contains("forbidden")
                || stderr_lower.contains("403")
                || stderr_lower.contains("access denied")
                || stderr_lower.contains("credential")
            {
                AgentErrorKind::Authentication
            } else if stderr_lower.contains("network") || stderr_lower.contains("connection") {
                AgentErrorKind::Network
            } else if stderr_lower.contains("model")
                && (stderr_lower.contains("not found") || stderr_lower.contains("unavailable"))
            {
                AgentErrorKind::ModelUnavailable
            } else if stderr_lower.contains("parse")
                || stderr_lower.contains("invalid")
                || stderr_lower.contains("malformed")
            {
                AgentErrorKind::ParsingError
            } else if stderr_lower.contains("permission denied")
                || stderr_lower.contains("operation not permitted")
                || stderr_lower.contains("no such file")
            {
                AgentErrorKind::FileSystem
            } else {
                AgentErrorKind::InternalError
            }
        }
    }
}

fn is_timeout_stderr(stderr_lower: &str) -> bool {
    // Be conservative: prioritize patterns that strongly indicate a timeout, and avoid
    // classifying generic network errors as timeouts unless the message says so.
    //
    // Examples observed across providers / runtimes:
    // - "Connection timeout" / "connection timed out"
    // - "timed out"
    // - "ETIMEDOUT"
    // - "deadline exceeded"
    // - "context deadline exceeded"
    contains_timeout_phrase(stderr_lower)
}

fn contains_timeout_phrase(text_lower: &str) -> bool {
    const TIMEOUT_PHRASES: [&str; 11] = [
        "timed out",
        "i/o timeout",
        "io timeout",
        "request timeout",
        "connection timeout",
        "connection timed out",
        "timeout while",
        "timeout waiting",
        "timeout occurred",
        "deadline exceeded",
        "context deadline exceeded",
    ];

    if text_lower.contains("etimedout") {
        return true;
    }

    TIMEOUT_PHRASES
        .iter()
        .any(|timeout_phrase| text_lower.contains(timeout_phrase))
}

/// Classify I/O error during agent execution.
#[must_use]
pub fn classify_io_error(error: &std::io::Error) -> AgentErrorKind {
    match error.kind() {
        std::io::ErrorKind::TimedOut => AgentErrorKind::Timeout,
        std::io::ErrorKind::PermissionDenied | std::io::ErrorKind::NotFound => {
            AgentErrorKind::FileSystem
        }
        std::io::ErrorKind::BrokenPipe
        | std::io::ErrorKind::ConnectionAborted
        | std::io::ErrorKind::ConnectionRefused
        | std::io::ErrorKind::ConnectionReset
        | std::io::ErrorKind::NotConnected
        | std::io::ErrorKind::AddrInUse
        | std::io::ErrorKind::AddrNotAvailable
        | std::io::ErrorKind::UnexpectedEof => AgentErrorKind::Network,
        _ => {
            // Some process/executor paths surface `io::ErrorKind::Other` with a message that still
            // carries useful intent; keep message-based heuristics as a fallback.
            let error_msg = error.to_string().to_lowercase();

            if contains_timeout_phrase(&error_msg) {
                AgentErrorKind::Timeout
            } else if error_msg.contains("permission")
                || error_msg.contains("access denied")
                || error_msg.contains("no such file")
                || error_msg.contains("not found")
            {
                AgentErrorKind::FileSystem
            } else if error_msg.contains("broken pipe") || error_msg.contains("connection") {
                AgentErrorKind::Network
            } else {
                AgentErrorKind::InternalError
            }
        }
    }
}

/// Determine if agent error is retriable.
///
/// Retriable errors should trigger model fallback (same agent, different model).
/// Non-retriable errors are reported as facts; the reducer decides retry vs fallback.
///
/// # Non-retriable errors with dedicated fact events:
///
/// - **`RateLimit` (429)**: Emitted as `AgentEvent::RateLimited` with prompt context.
///   The reducer typically switches to the next agent immediately.
///
/// - **Timeout**: Emitted as `AgentEvent::TimedOut`.
///   The reducer retries the same agent first and only falls back after exhausting
///   the configured retry budget.
///
/// - **Authentication**: Emitted as `AgentEvent::AuthFailed`.
///   The reducer typically switches to the next agent immediately.
#[must_use]
pub const fn is_retriable_agent_error(error_kind: &AgentErrorKind) -> bool {
    matches!(
        error_kind,
        AgentErrorKind::Network | AgentErrorKind::ModelUnavailable
    )
}

/// Check if an error kind represents a timeout error.
#[must_use]
pub const fn is_timeout_error(error_kind: &AgentErrorKind) -> bool {
    matches!(error_kind, AgentErrorKind::Timeout)
}

/// Check if an error kind represents a rate limit (429) error.
///
/// Rate limit errors are emitted as `AgentEvent::RateLimited` instead of a generic
/// `InvocationFailed` so the reducer can apply deterministic policy.
#[must_use]
pub const fn is_rate_limit_error(error_kind: &AgentErrorKind) -> bool {
    matches!(error_kind, AgentErrorKind::RateLimit)
}

/// Check if an error kind represents an authentication error.
///
/// Auth errors are emitted as `AgentEvent::AuthFailed` instead of a generic
/// `InvocationFailed` so the reducer can apply deterministic policy.
#[must_use]
pub const fn is_auth_error(error_kind: &AgentErrorKind) -> bool {
    matches!(error_kind, AgentErrorKind::Authentication)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classify_agent_error_does_not_treat_filename_timeout_rs_as_timeout() {
        // Regression test: naive `contains("timeout")` matching can incorrectly classify
        // compiler/file path diagnostics (e.g., `timeout.rs:1:1`) as a timeout error.
        let error_kind = classify_agent_error(1, "timeout.rs:1:1: error: unexpected token", None);

        assert_eq!(error_kind, AgentErrorKind::InternalError);
    }

    #[test]
    fn classify_agent_error_detects_usage_limit_even_if_first_match_is_filename() {
        // Regression test: if stderr contains multiple occurrences of "usage limit" and the first
        // one looks like a filename (e.g., "usage_limit.rs"), we must still detect a later
        // provider error like "anthropic: usage limit".
        // Note: some diagnostics include filenames with spaces (e.g., "usage limit.rs").
        // This should not suppress detection when a real provider usage-limit error appears later.
        let stderr = "error: usage limit.rs file not found\nanthropic: usage limit";

        let error_kind = classify_agent_error(1, stderr, None);

        assert_eq!(error_kind, AgentErrorKind::RateLimit);
    }
}
