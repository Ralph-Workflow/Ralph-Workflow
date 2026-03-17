//! Run context for tracking pipeline execution lineage and state.
//!
//! This module provides the `RunContext` struct which tracks information
//! about a pipeline run including its unique identifier, parent run (if resumed),
//! and actual execution counts (separate from configured counts).

use serde::{Deserialize, Serialize};

/// Context for tracking pipeline execution lineage and state.
///
/// This tracks information about the current pipeline run that is separate
/// from the configured parameters. It enables:
/// - Unique identification of each run (`run_id`)
/// - Tracking resume lineage (`parent_run_id`)
/// - Counting how many times a session has been resumed (`resume_count`)
/// - Tracking actual completed iterations vs configured iterations
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunContext {
    /// Unique identifier for this run (UUID v4)
    pub run_id: String,
    /// Parent run ID if this is a resumed session
    pub parent_run_id: Option<String>,
    /// Number of times this session has been resumed
    pub resume_count: u32,
    /// Actual number of developer iterations that have completed
    pub actual_developer_runs: u32,
    /// Actual number of reviewer passes that have completed
    pub actual_reviewer_runs: u32,
}

impl RunContext {
    /// Create a new `RunContext` for a fresh run.
    #[must_use]
    pub fn new() -> Self {
        Self {
            run_id: uuid::Uuid::new_v4().to_string(),
            parent_run_id: None,
            resume_count: 0,
            actual_developer_runs: 0,
            actual_reviewer_runs: 0,
        }
    }

    /// Create a `RunContext` from a checkpoint (for resume scenarios).
    #[must_use]
    pub fn from_checkpoint(checkpoint: &super::PipelineCheckpoint) -> Self {
        Self {
            run_id: uuid::Uuid::new_v4().to_string(), // New run_id for resumed run
            parent_run_id: Some(checkpoint.run_id.clone()),
            resume_count: checkpoint.resume_count.saturating_add(1),
            actual_developer_runs: checkpoint.actual_developer_runs,
            actual_reviewer_runs: checkpoint.actual_reviewer_runs,
        }
    }

    /// Update the actual developer runs count.
    #[cfg(test)]
    #[must_use]
    pub const fn with_developer_runs(mut self, runs: u32) -> Self {
        self.actual_developer_runs = runs;
        self
    }

    /// Update the actual reviewer runs count.
    #[cfg(test)]
    #[must_use]
    pub const fn with_reviewer_runs(mut self, runs: u32) -> Self {
        self.actual_reviewer_runs = runs;
        self
    }

    /// Record a completed developer iteration.
    pub const fn record_developer_iteration(&mut self) {
        self.actual_developer_runs = self.actual_developer_runs.saturating_add(1);
    }

    /// Record a completed reviewer pass.
    pub const fn record_reviewer_pass(&mut self) {
        self.actual_reviewer_runs = self.actual_reviewer_runs.saturating_add(1);
    }
}

impl Default for RunContext {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::checkpoint::state::{
        AgentConfigSnapshot, CheckpointParams, CliArgsSnapshot, PipelineCheckpoint, PipelinePhase,
        RebaseState,
    };

    #[test]
    fn test_run_context_new() {
        let ctx = RunContext::new();
        assert!(!ctx.run_id.is_empty());
        assert!(ctx.parent_run_id.is_none());
        assert_eq!(ctx.resume_count, 0);
        assert_eq!(ctx.actual_developer_runs, 0);
        assert_eq!(ctx.actual_reviewer_runs, 0);
    }

    #[test]
    fn test_run_context_from_checkpoint() {
        // Create a mock checkpoint
        let cli_args = CliArgsSnapshot::new(5, 2, None, true, 2, false, None);
        let dev_config =
            AgentConfigSnapshot::new("claude".into(), "cmd".into(), "-o".into(), None, true);
        let rev_config =
            AgentConfigSnapshot::new("codex".into(), "cmd".into(), "-o".into(), None, true);

        let original_run_id = uuid::Uuid::new_v4().to_string();
        let checkpoint = PipelineCheckpoint::from_params(CheckpointParams {
            phase: PipelinePhase::Development,
            iteration: 2,
            total_iterations: 5,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            developer_agent: "claude",
            reviewer_agent: "codex",
            cli_args,
            developer_agent_config: dev_config,
            reviewer_agent_config: rev_config,
            rebase_state: RebaseState::default(),
            git_user_name: None,
            git_user_email: None,
            run_id: &original_run_id,
            parent_run_id: None,
            resume_count: 1,
            actual_developer_runs: 2,
            actual_reviewer_runs: 0,
            working_dir: "/test/repo".to_string(),
            prompt_md_checksum: None,
            config_path: None,
            config_checksum: None,
        });

        let run_ctx = RunContext::from_checkpoint(&checkpoint);

        assert_ne!(
            run_ctx.run_id, original_run_id,
            "new run_id should be generated"
        );
        assert_eq!(run_ctx.parent_run_id, Some(original_run_id));
        assert_eq!(run_ctx.resume_count, 2, "resume_count should increment");
        assert_eq!(run_ctx.actual_developer_runs, 2);
        assert_eq!(run_ctx.actual_reviewer_runs, 0);
    }

    #[test]
    fn test_run_context_with_developer_runs() {
        let ctx = RunContext::new().with_developer_runs(5);
        assert_eq!(ctx.actual_developer_runs, 5);
    }

    #[test]
    fn test_run_context_with_reviewer_runs() {
        let ctx = RunContext::new().with_reviewer_runs(3);
        assert_eq!(ctx.actual_reviewer_runs, 3);
    }
}
