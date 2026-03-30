//! Log file naming utilities.
//!
//! This module provides functions for building log file paths with various
//! naming conventions (legacy and simplified per-run formats).

/// Sanitize an agent name for use in file paths.
///
/// Replaces slashes with hyphens to avoid creating subdirectories.
#[must_use]
pub fn sanitize_agent_name(agent_name: &str) -> String {
    agent_name.replace('/', "-")
}

/// Build a legacy-style log file path from components.
///
/// This generates a log filename with the pattern:
/// `\{prefix\}_\{agent\}_\{model_index\}.log`
///
/// This is the **legacy naming convention** used before per-run logging was introduced.
/// It is retained for special-purpose logs (e.g., commit generation, conflict resolution)
/// where embedding agent identity in the filename is useful for tooling.
///
/// For new per-run agent logs, use [`RunLogContext::agent_log`](crate::logging::RunLogContext::agent_log)
/// instead, which uses the simplified `{phase}_{index}[_aN].log` format.
///
/// # Arguments
///
/// * `prefix` - Log prefix path (e.g., ".`agent/logs/commit_generation/commit_generation`")
/// * `agent_name` - Agent identifier (will be sanitized to replace `/` with `-`)
/// * `model_index` - Model index for multi-model agents
///
/// # Returns
///
/// A log file path string with the legacy naming format.
#[must_use]
pub fn build_logfile_path(prefix: &str, agent_name: &str, model_index: usize) -> String {
    let safe_agent_name = sanitize_agent_name(agent_name);
    format!("{prefix}_{safe_agent_name}_{model_index}.log")
}

/// Build a legacy-style log file path with retry attempt index.
///
/// This generates a log filename with the pattern:
/// `{prefix}_{agent}_{model_index}_a{attempt}.log`
///
/// This is the **legacy naming convention** used before per-run logging was introduced.
/// The attempt suffix distinguishes between multiple invocations (e.g., during XSD retry
/// cycles or after timeout-triggered agent switches).
///
/// It is retained for special-purpose logs (e.g., commit generation, conflict resolution)
/// where embedding agent identity in the filename is useful for tooling.
///
/// For new per-run agent logs, use [`RunLogContext::agent_log`](crate::logging::RunLogContext::agent_log)
/// instead, which uses the simplified `{phase}_{index}[_aN].log` format.
///
/// # Arguments
///
/// * `prefix` - Log prefix path (e.g., ".`agent/logs/commit_generation/commit_generation`")
/// * `agent_name` - Agent identifier (will be sanitized to replace `/` with `-`)
/// * `model_index` - Model index for multi-model agents
/// * `attempt` - Retry attempt counter (0 for first retry, 1 for second retry, etc.)
///
/// # Returns
///
/// A log file path string with the legacy naming format including attempt suffix.
#[must_use]
pub fn build_logfile_path_with_attempt(
    prefix: &str,
    agent_name: &str,
    model_index: usize,
    attempt: u32,
) -> String {
    let safe_agent_name = sanitize_agent_name(agent_name);
    format!("{prefix}_{safe_agent_name}_{model_index}_a{attempt}.log")
}
