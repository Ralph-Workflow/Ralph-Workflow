//! Fault-tolerant agent executor.
//!
//! This module provides bulletproof agent execution wrapper that:
//! - Catches all panics from subprocess execution
//! - Catches all I/O errors and non-zero exit codes
//! - Never returns errors - always emits `PipelineEvents`
//! - Provides detailed error classification for reducer-driven retry/fallback policy
//! - Logs all failures but continues pipeline execution
//!
//! Key design principle: **Agent failures should NEVER crash the pipeline**.

mod error_classification;

#[cfg(test)]
mod tests;

use crate::agents::{AgentRole, JsonParserType};
use crate::common::domain_types::AgentName;
use crate::logger::Loggable;
use crate::pipeline::{run_with_prompt, PipelineRuntime, PromptCommand};
use crate::reducer::event::{AgentErrorKind, PipelineEvent, TimeoutOutputKind};
use crate::workspace::Workspace;
use anyhow::Result;

// Re-export error classification functions for use by other modules
pub use error_classification::{
    classify_agent_error, classify_io_error, is_auth_error, is_rate_limit_error,
    is_retriable_agent_error, is_timeout_error,
};

const ERROR_PREVIEW_MAX_CHARS: usize = 100;

/// Result of executing an agent.
///
/// Contains the pipeline event and optional `session_id` for session continuation.
///
/// # Session ID Handling
///
/// When `session_id` is `Some`, the handler MUST emit a separate `SessionEstablished`
/// event to the reducer. This is the proper way to handle session IDs in the reducer
/// architecture - each piece of information is communicated via a dedicated event.
///
/// The handler should:
/// 1. Process `event` through the reducer
/// 2. If `session_id.is_some()`, emit `SessionEstablished` and process it
///
/// This two-event approach ensures:
/// - Clean separation of concerns (success vs session establishment)
/// - Proper state transitions in the reducer
/// - Session ID is stored in `agent_chain.last_session_id` for XSD retry reuse
pub struct AgentExecutionResult {
    /// The pipeline event from agent execution (success or failure).
    pub event: PipelineEvent,
    /// Session ID from agent's init event, for XSD retry session continuation.
    ///
    /// When present, handler must emit `SessionEstablished` event separately.
    pub session_id: Option<String>,
}

/// Configuration for fault-tolerant agent execution.
#[derive(Clone, Copy)]
pub struct AgentExecutionConfig<'a> {
    /// Agent role (developer, reviewer, commit agent)
    pub role: AgentRole,
    /// Agent name from registry
    pub agent_name: &'a str,
    /// Agent command to execute
    pub cmd_str: &'a str,
    /// JSON parser type
    pub parser_type: JsonParserType,
    /// Environment variables for agent
    pub env_vars: &'a std::collections::HashMap<String, String>,
    /// Prompt to send to agent
    pub prompt: &'a str,
    /// Display name for logging
    pub display_name: &'a str,
    /// Log prefix (without extension) used to associate artifacts.
    ///
    /// Example: `.agent/logs/planning_1`.
    pub log_prefix: &'a str,
    /// Model fallback index for attribution.
    pub model_index: usize,
    /// Attempt counter for attribution.
    pub attempt: u32,
    /// Log file path
    pub logfile: &'a str,
    /// Path to the file this phase is expected to produce.
    ///
    /// When set, the idle timeout monitor uses its existence as a
    /// "complete-but-waiting" signal: if the file exists and the process is
    /// idle, the process is killed and the phase advances as success.
    pub completion_output_path: Option<&'a std::path::Path>,
}

/// Execute an agent with bulletproof error handling.
///
/// This function:
/// 1. Uses `catch_unwind` to catch panics from subprocess
/// 2. Catches all I/O errors and non-zero exit codes
/// 3. Never returns errors - always emits `PipelineEvents`
/// 4. Classifies errors for retry/fallback decisions
/// 5. Logs failures but continues pipeline
///
/// # Arguments
///
/// * `config` - Agent execution configuration
/// * `runtime` - Pipeline runtime
///
/// # Returns
///
/// Returns `Ok(AgentExecutionResult)` with:
/// - `event`: `AgentInvocationSucceeded` or `AgentInvocationFailed`
/// - `session_id`: Optional session ID for XSD retry session continuation
///
/// The handler MUST emit `SessionEstablished` as a separate event when `session_id`
/// is present. This ensures proper state management in the reducer.
///
/// This function never returns `Err` - all errors are converted to events.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn execute_agent_fault_tolerantly(
    config: AgentExecutionConfig<'_>,
    runtime: &mut PipelineRuntime<'_>,
) -> Result<AgentExecutionResult> {
    let role = config.role;
    let agent_name = AgentName::from(config.agent_name.to_string());

    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        try_agent_execution(config, runtime)
    }));

    Ok(result.unwrap_or_else(|_| {
        let error_kind = AgentErrorKind::InternalError;
        let retriable = is_retriable_agent_error(&error_kind);

        AgentExecutionResult {
            event: PipelineEvent::agent_invocation_failed(
                role,
                agent_name.clone(),
                1,
                error_kind,
                retriable,
            ),
            session_id: None,
        }
    }))
}

/// Try to execute agent without panic catching.
///
/// This function does the actual agent execution and returns
/// either success or failure events. It's wrapped by
/// `execute_agent_fault_tolerantly` which handles panics.
fn try_agent_execution(
    config: AgentExecutionConfig<'_>,
    runtime: &mut PipelineRuntime<'_>,
) -> AgentExecutionResult {
    let agent_name = AgentName::from(config.agent_name.to_string());
    let prompt_cmd = PromptCommand {
        label: config.agent_name,
        display_name: config.display_name,
        cmd_str: config.cmd_str,
        prompt: config.prompt,
        log_prefix: config.log_prefix,
        model_index: Some(config.model_index),
        attempt: Some(config.attempt),
        logfile: config.logfile,
        parser_type: config.parser_type,
        env_vars: config.env_vars,
        completion_output_path: config.completion_output_path,
    };

    match run_with_prompt(&prompt_cmd, runtime) {
        Ok(result) if result.exit_code == 0 => AgentExecutionResult {
            event: PipelineEvent::agent_invocation_succeeded(config.role, agent_name.clone()),
            session_id: result.session_id,
        },
        Ok(result) => classify_nonzero_command_result(result, &config, &agent_name, runtime),
        Err(e) => {
            // `run_with_prompt` returns `io::Error` directly. Classify based on the error kind
            // instead of attempting to downcast the inner error payload.
            let error_kind = classify_io_error(&e);

            // Result-file pre-check: a valid result file means the agent completed its work
            // before run_with_prompt failed. Treat as success regardless of the I/O error.
            if let Some(path) = config.completion_output_path {
                if crate::files::llm_output_extraction::has_valid_xml_output(
                    runtime.workspace,
                    path,
                ) {
                    return AgentExecutionResult {
                        event: PipelineEvent::agent_invocation_succeeded(
                            config.role,
                            agent_name.clone(),
                        ),
                        session_id: None,
                    };
                }
            }

            // I/O errors from run_with_prompt itself (e.g., prompt file write failure, spawn
            // failure) are NOT wall-clock timeouts — they never carry `timeout_context`.
            //
            // TIMEOUT CONTRACT: only `timeout_context: Some(_)` (set by the idle-timeout
            // monitor in the Ok path) constitutes definitive timeout evidence. An I/O error
            // classified as `AgentErrorKind::Timeout` (e.g., `io::ErrorKind::TimedOut` from a
            // filesystem write) is an infrastructure failure, not a wall-clock idle timeout.
            // Always emit InvocationFailed here — never TimedOut.
            let retriable = is_retriable_agent_error(&error_kind);

            AgentExecutionResult {
                event: PipelineEvent::agent_invocation_failed(
                    config.role,
                    agent_name.clone(),
                    1,
                    error_kind,
                    retriable,
                ),
                session_id: None,
            }
        }
    }
}

/// Classify a non-zero (and non-success) `CommandResult` into an `AgentExecutionResult`.
///
/// This function implements the full classification matrix for non-zero exits:
///
/// | timeout_context | Valid result file | Expected event          |
/// |-----------------|------------------|-------------------------|
/// | None            | Yes              | InvocationSucceeded     |
/// | None            | No               | InvocationFailed        |
/// | Some            | Yes              | InvocationSucceeded     |
/// | Some            | No (absent)      | TimedOut(NoResult)      |
/// | Some            | No (invalid XML) | TimedOut(PartialResult) |
///
/// # Classification order (mandatory)
///
/// 1. **Result file check (highest priority)** — a valid result file overrides ALL other
///    signals, including rate limit, auth failure, and explicit timeout evidence.
/// 2. Rate limit — emit `AgentRateLimited` for immediate agent fallback.
/// 3. Auth failure — emit `AgentAuthFailed` for immediate agent fallback.
/// 4. Explicit timeout — emit `TimedOut` with output kind classification.
/// 5. Anything else — emit `InvocationFailed` with classified error kind.
///
/// Note: the exit_code == 0 case is handled by the caller before reaching this function.
pub(crate) fn classify_nonzero_command_result(
    result: crate::pipeline::CommandResult,
    config: &AgentExecutionConfig<'_>,
    agent_name: &AgentName,
    runtime: &PipelineRuntime<'_>,
) -> AgentExecutionResult {
    let exit_code = result.exit_code;
    let has_explicit_timeout = result.timeout_context.is_some();

    // Extract error message from logfile (stdout) for agents that emit errors as JSON.
    // This is critical for OpenCode and similar agents that don't use stderr for errors.
    let stdout_error =
        crate::pipeline::extract_error_identifier_from_logfile(config.logfile, runtime.workspace);

    // Log extracted stdout errors only in debug verbosity.
    if runtime.config.verbosity.is_debug() {
        if let Some(ref err_msg) = stdout_error {
            runtime.logger.log(&format!(
                "[DEBUG] [OpenCode] Extracted error from logfile for agent '{}': {}",
                config.agent_name, err_msg
            ));
        }
    }

    let error_kind = classify_agent_error(exit_code, &result.stderr, stdout_error.as_deref());

    // RESULT FILE PRE-CHECK — highest priority, overrides all error signals.
    //
    // A valid result file is definitive evidence that the agent completed its
    // work successfully, regardless of what error signal fired after the fact:
    //
    // - Proprietary exit codes (e.g., reason:91 from OpenCode) — Bug 1
    // - Rate limit detected in stderr/stdout after the agent finished writing
    // - Auth failure signal emitted after work was already done
    // - Explicit timeout (wall-clock exceeded) after a valid result was produced
    //
    // This check MUST happen before any error classification early-returns so
    // that a completed result cannot be misclassified as a failure.
    if let Some(path) = config.completion_output_path {
        if crate::files::llm_output_extraction::has_valid_xml_output(runtime.workspace, path) {
            return AgentExecutionResult {
                event: PipelineEvent::agent_invocation_succeeded(config.role, agent_name.clone()),
                session_id: result.session_id,
            };
        }
    }

    // Rate limit: emit fact event with prompt context for immediate agent fallback.
    if is_rate_limit_error(&error_kind) {
        let error_source = if stdout_error.is_some() {
            "stdout"
        } else {
            "stderr"
        };
        let error_preview = stdout_error
            .as_deref()
            .or(Some(result.stderr.as_str()))
            .unwrap_or("");
        let preview = build_error_preview(error_preview, ERROR_PREVIEW_MAX_CHARS);
        runtime.logger.info(&format!(
            "[OpenCode] Rate limit detected for agent '{}' (source: {}): {}",
            config.agent_name, error_source, preview
        ));

        return AgentExecutionResult {
            event: PipelineEvent::agent_rate_limited(
                config.role,
                agent_name.clone(),
                Some(config.prompt.to_string()),
            ),
            session_id: None,
        };
    }

    // Auth failure: emit fact event without prompt context.
    if is_auth_error(&error_kind) {
        return AgentExecutionResult {
            event: PipelineEvent::agent_auth_failed(config.role, agent_name.clone()),
            session_id: None,
        };
    }

    // Explicit timeout: emit fact event with output kind classification.
    //
    // TIMEOUT CLASSIFICATION RULE:
    // `result.timeout_context.is_some()` is the DEFINITIVE signal that a real wall-clock
    // timeout was enforced by the idle-timeout monitor. Exit code 143 (SIGTERM) alone
    // is NOT sufficient evidence — SIGTERM may be sent for other reasons.
    //
    // The result-file pre-check above has already promoted a valid result to success.
    // Reaching here means the result file is absent or invalid — classify by output kind.
    if has_explicit_timeout {
        let output_kind = determine_timeout_output_kind(
            config.logfile,
            config.completion_output_path,
            runtime.workspace,
        );
        return AgentExecutionResult {
            event: PipelineEvent::agent_timed_out(
                config.role,
                agent_name.clone(),
                output_kind,
                Some(config.logfile.to_string()),
                result.child_status_at_timeout,
            ),
            session_id: None,
        };
    }

    // If we reach here, no explicit timeout evidence exists.
    // SIGTERM (exit 143) without `timeout_context` is classified by
    // `classify_agent_error` as `AgentErrorKind::Timeout`, but it must NOT be
    // promoted to a `TimedOut` event — fall through to `InvocationFailed`.
    let retriable = is_retriable_agent_error(&error_kind);

    AgentExecutionResult {
        event: PipelineEvent::agent_invocation_failed(
            config.role,
            agent_name.clone(),
            exit_code,
            error_kind,
            retriable,
        ),
        session_id: None,
    }
}

fn build_error_preview(message: &str, max_chars: usize) -> String {
    message.chars().take(max_chars).collect()
}

/// Minimum non-whitespace characters to classify as meaningful output.
///
/// Used only in the logfile-heuristic fallback path (when no `completion_output_path`
/// is configured, e.g. Analysis drain).
const MEANINGFUL_OUTPUT_THRESHOLD: usize = 10;

/// Determine whether a timed-out agent produced a valid result.
///
/// When a `completion_output_path` is provided, classification is based on
/// whether that file exists on disk:
/// - File present (even if invalid XML) → `PartialResult`
/// - File absent → `NoResult`
///
/// Note: callers MUST check `has_valid_xml_output` BEFORE calling this function
/// and promote a valid result to success. By the time this function is reached,
/// the valid-result case has already been handled.
///
/// When no `completion_output_path` is provided (e.g., Analysis drain), falls
/// back to logfile content heuristic.
///
/// # Fail-Safe Behavior
///
/// If the logfile cannot be read in the fallback path, returns `NoResult` to
/// trigger immediate agent switching rather than retrying a potentially broken agent.
fn determine_timeout_output_kind(
    logfile_path: &str,
    completion_output_path: Option<&std::path::Path>,
    workspace: &dyn Workspace,
) -> TimeoutOutputKind {
    // When a completion file is expected, classify based on file existence.
    // The valid-file case is handled upstream; here we distinguish missing vs. present-but-invalid.
    if let Some(path) = completion_output_path {
        return if workspace.exists(path) {
            TimeoutOutputKind::PartialResult
        } else {
            TimeoutOutputKind::NoResult
        };
    }

    // Fallback: no completion path configured (e.g. Analysis drain).
    // Use logfile content as a proxy for whether the agent did any real work.
    let Some(content) = workspace.read(std::path::Path::new(logfile_path)).ok() else {
        return TimeoutOutputKind::NoResult;
    };

    let non_whitespace_count = content.chars().filter(|c| !c.is_whitespace()).count();
    if non_whitespace_count >= MEANINGFUL_OUTPUT_THRESHOLD {
        TimeoutOutputKind::PartialResult
    } else {
        TimeoutOutputKind::NoResult
    }
}
