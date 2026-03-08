//! State restoration from checkpoints.
//!
//! This module provides functionality to restore pipeline state from a checkpoint,
//! including CLI arguments and configuration overrides.

use crate::checkpoint::execution_history::ExecutionHistory;
use crate::checkpoint::state::{PipelineCheckpoint, PipelinePhase, RebaseState};
use crate::config::Config;

/// Rich context about a resumed session for use in agent prompts.
///
/// This struct contains information that helps AI agents understand where
/// they are in the pipeline when resuming from a checkpoint, enabling them
/// to provide more contextual and appropriate responses.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResumeContext {
    /// The phase being resumed from
    pub phase: PipelinePhase,
    /// Current iteration number (for development)
    pub iteration: u32,
    /// Total iterations
    pub total_iterations: u32,
    /// Current reviewer pass
    pub reviewer_pass: u32,
    /// Total reviewer passes
    pub total_reviewer_passes: u32,
    /// Number of times this session has been resumed
    pub resume_count: u32,
    /// Rebase state if applicable
    pub rebase_state: RebaseState,
    /// Run ID for tracing
    pub run_id: String,
    /// Captured prompts from the original run for deterministic replay
    pub prompt_history: Option<std::collections::HashMap<String, String>>,
    /// Execution history from the checkpoint (if available)
    pub execution_history: Option<ExecutionHistory>,
}

impl ResumeContext {
    /// Display name for the current phase.
    #[must_use]
    pub fn phase_name(&self) -> String {
        match self.phase {
            PipelinePhase::Rebase => "Rebase".to_string(),
            PipelinePhase::Planning => "Planning".to_string(),
            PipelinePhase::Development => format!(
                "Development iteration {}/{}",
                self.iteration + 1,
                self.total_iterations
            ),
            PipelinePhase::Review => format!(
                "Review (pass {}/{})",
                self.reviewer_pass + 1,
                self.total_reviewer_passes
            ),
            PipelinePhase::CommitMessage => "Commit Message Generation".to_string(),
            PipelinePhase::FinalValidation => "Final Validation".to_string(),
            PipelinePhase::Complete => "Complete".to_string(),
            PipelinePhase::PreRebase => "Pre-Rebase".to_string(),
            PipelinePhase::PreRebaseConflict => "Pre-Rebase Conflict".to_string(),
            PipelinePhase::PostRebase => "Post-Rebase".to_string(),
            PipelinePhase::PostRebaseConflict => "Post-Rebase Conflict".to_string(),
            PipelinePhase::AwaitingDevFix => "Awaiting Dev Fix".to_string(),
            PipelinePhase::Interrupted => "Interrupted".to_string(),
        }
    }
}

impl PipelineCheckpoint {
    /// Extract rich resume context from this checkpoint.
    ///
    /// This method creates a `ResumeContext` containing all the information
    /// needed to generate informative prompts for agents when resuming.
    #[must_use]
    pub fn resume_context(&self) -> ResumeContext {
        ResumeContext {
            phase: self.phase,
            iteration: self.iteration,
            total_iterations: self.total_iterations,
            reviewer_pass: self.reviewer_pass,
            total_reviewer_passes: self.total_reviewer_passes,
            resume_count: self.resume_count,
            rebase_state: self.rebase_state.clone(),
            run_id: self.run_id.clone(),
            prompt_history: self.prompt_history.clone(),
            execution_history: self.execution_history.clone(),
        }
    }
}

/// Apply checkpoint values to the current config for deterministic resume.
pub fn apply_checkpoint_to_config(config: &mut Config, checkpoint: &PipelineCheckpoint) {
    let cli_args = &checkpoint.cli_args;

    // Always restore developer_iters and reviewer_reviews from checkpoint
    // to ensure exact state restoration, even if zero
    config.developer_iters = cli_args.developer_iters;
    config.reviewer_reviews = cli_args.reviewer_reviews;

    // Note: review_depth is stored as a string in the checkpoint
    // but as an enum in Config. For now, we don't override it.
    // This could be enhanced to parse the string back to an enum.

    // Apply model overrides if they exist in the checkpoint
    if let Some(ref model) = checkpoint.developer_agent_config.model_override {
        config.developer_model = Some(model.clone());
    }
    if let Some(ref model) = checkpoint.reviewer_agent_config.model_override {
        config.reviewer_model = Some(model.clone());
    }

    // Apply provider overrides if they exist in the checkpoint
    if let Some(ref provider) = checkpoint.developer_agent_config.provider_override {
        config.developer_provider = Some(provider.clone());
    }
    if let Some(ref provider) = checkpoint.reviewer_agent_config.provider_override {
        config.reviewer_provider = Some(provider.clone());
    }

    // Apply context levels if they exist in the checkpoint
    config.developer_context = checkpoint.developer_agent_config.context_level;
    config.reviewer_context = checkpoint.reviewer_agent_config.context_level;

    // Apply git identity if it exists in the checkpoint
    if let Some(ref name) = checkpoint.git_user_name {
        config.git_user_name = Some(name.clone());
    }
    if let Some(ref email) = checkpoint.git_user_email {
        config.git_user_email = Some(email.clone());
    }

    // Always restore isolation_mode from checkpoint for exact state restoration
    config.isolation_mode = cli_args.isolation_mode;

    // Apply verbosity level from checkpoint
    config.verbosity = crate::config::types::Verbosity::from(cli_args.verbosity);

    // Apply show_streaming_metrics from checkpoint
    config.show_streaming_metrics = cli_args.show_streaming_metrics;

    // Apply reviewer_json_parser from checkpoint if it exists
    if let Some(ref parser) = cli_args.reviewer_json_parser {
        config.reviewer_json_parser = Some(parser.clone());
    }
}

/// Restore environment variables from a checkpoint.
///
/// Restore safe environment variables from the checkpoint snapshot.
#[must_use]
pub fn restore_environment_from_checkpoint(checkpoint: &PipelineCheckpoint) -> usize {
    restore_environment_impl(checkpoint, |key, value| {
        std::env::set_var(key, value);
    })
}

/// Inner implementation for restoring environment variables from a checkpoint.
///
/// Accepts an injectable `set_var` callback so tests can verify which variables
/// would be set without mutating the real process environment (eliminating the
/// need for `#[serial]`).
pub(crate) fn restore_environment_impl(
    checkpoint: &PipelineCheckpoint,
    mut set_var: impl FnMut(&str, &str),
) -> usize {
    let Some(ref env_snap) = checkpoint.env_snapshot else {
        return 0;
    };

    let mut restored = 0;

    // Restore RALPH_* variables (safe only)
    for (key, value) in &env_snap.ralph_vars {
        if crate::checkpoint::state::is_sensitive_env_key(key) {
            continue;
        }
        set_var(key, value);
        restored += 1;
    }

    // Restore other relevant variables (safe only)
    for (key, value) in &env_snap.other_vars {
        if crate::checkpoint::state::is_sensitive_env_key(key) {
            continue;
        }
        set_var(key, value);
        restored += 1;
    }

    restored
}

/// Calculate the starting iteration for development phase resume.
#[must_use]
pub fn calculate_start_iteration(checkpoint: &PipelineCheckpoint, max_iterations: u32) -> u32 {
    match checkpoint.phase {
        PipelinePhase::Planning | PipelinePhase::Development => {
            checkpoint.iteration.clamp(1, max_iterations)
        }
        // For later phases, development is already complete
        _ => max_iterations,
    }
}

/// Calculate the starting reviewer pass for review phase resume.
///
/// When resuming from a checkpoint in the review phase, this determines
/// which pass to start from based on the checkpoint state.
///
/// # Arguments
///
/// * `checkpoint` - The checkpoint to calculate from
/// * `max_passes` - Maximum review passes configured
///
/// # Returns
///
/// The pass number to start from (1-indexed).
#[must_use]
pub fn calculate_start_reviewer_pass(checkpoint: &PipelineCheckpoint, max_passes: u32) -> u32 {
    match checkpoint.phase {
        PipelinePhase::Review => checkpoint.reviewer_pass.clamp(1, max_passes.max(1)),
        // For earlier phases, start from the beginning
        PipelinePhase::Planning
        | PipelinePhase::Development
        | PipelinePhase::PreRebase
        | PipelinePhase::PreRebaseConflict => 1,
        // For later phases, review is already complete
        _ => max_passes,
    }
}

/// Determine if a phase should be skipped based on checkpoint.
///
/// Returns true if the checkpoint indicates this phase has already been completed.
#[must_use]
pub const fn should_skip_phase(phase: PipelinePhase, checkpoint: &PipelineCheckpoint) -> bool {
    phase_rank(phase) < phase_rank(checkpoint.phase)
}

/// Get the rank (position) of a phase in the pipeline.
///
/// Lower values indicate earlier phases in the pipeline.
const fn phase_rank(phase: PipelinePhase) -> u32 {
    match phase {
        PipelinePhase::Planning => 0,
        PipelinePhase::Development => 1,
        PipelinePhase::CommitMessage => 3,
        PipelinePhase::FinalValidation => 4,
        PipelinePhase::Complete => 5,
        PipelinePhase::AwaitingDevFix => 6,
        PipelinePhase::Interrupted => 7,
        // Review and rebase phases all map to rank 2
        PipelinePhase::Review
        | PipelinePhase::PreRebase
        | PipelinePhase::PreRebaseConflict
        | PipelinePhase::Rebase
        | PipelinePhase::PostRebase
        | PipelinePhase::PostRebaseConflict => 2,
    }
}

#[cfg(test)]
#[derive(Debug, Clone)]
pub struct RestoredContext {
    pub phase: PipelinePhase,
    pub resume_iteration: u32,
    pub total_iterations: u32,
    pub resume_reviewer_pass: u32,
    pub total_reviewer_passes: u32,
    pub developer_agent: String,
    pub reviewer_agent: String,
    pub cli_args: Option<crate::checkpoint::state::CliArgsSnapshot>,
}

#[cfg(test)]
impl RestoredContext {
    /// Create a restored context from a checkpoint.
    #[must_use]
    pub fn from_checkpoint(checkpoint: &PipelineCheckpoint) -> Self {
        // Determine if CLI args are meaningful (non-default values)
        let cli_args = if checkpoint.cli_args.developer_iters > 0
            || checkpoint.cli_args.reviewer_reviews > 0
        {
            Some(checkpoint.cli_args.clone())
        } else {
            None
        };

        Self {
            phase: checkpoint.phase,
            resume_iteration: checkpoint.iteration,
            total_iterations: checkpoint.total_iterations,
            resume_reviewer_pass: checkpoint.reviewer_pass,
            total_reviewer_passes: checkpoint.total_reviewer_passes,
            developer_agent: checkpoint.developer_agent.clone(),
            reviewer_agent: checkpoint.reviewer_agent.clone(),
            cli_args,
        }
    }

    /// Check if we should use checkpoint values for iteration counts.
    ///
    /// Returns true if the checkpoint has meaningful CLI args that should
    /// override the current configuration.
    #[must_use]
    pub fn should_use_checkpoint_iterations(&self) -> bool {
        self.cli_args
            .as_ref()
            .is_some_and(|args| args.developer_iters > 0)
    }

    /// Check if we should use checkpoint values for reviewer counts.
    #[must_use]
    pub fn should_use_checkpoint_reviewer_passes(&self) -> bool {
        self.cli_args
            .as_ref()
            .is_some_and(|args| args.reviewer_reviews > 0)
    }
}

#[cfg(test)]
mod tests;
