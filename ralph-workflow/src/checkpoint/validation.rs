//! Checkpoint validation for resume functionality.
//!
//! This module provides validation for checkpoint state before resuming,
//! ensuring the environment matches the checkpoint and detecting configuration changes.

use crate::agents::AgentRegistry;
use crate::checkpoint::state::{
    calculate_file_checksum_with_workspace, AgentConfigSnapshot, PipelineCheckpoint,
};
use crate::config::Config;
use crate::workspace::Workspace;
use std::path::Path;

/// Result of checkpoint validation.
#[derive(Debug)]
pub struct ValidationResult {
    /// Whether the checkpoint is valid for resume.
    pub is_valid: bool,
    /// Warnings that don't prevent resume but should be shown.
    pub warnings: Vec<String>,
    /// Errors that prevent resume.
    pub errors: Vec<String>,
}

impl ValidationResult {
    /// Create a successful validation result with no issues.
    #[must_use]
    pub const fn ok() -> Self {
        Self {
            is_valid: true,
            warnings: Vec::new(),
            errors: Vec::new(),
        }
    }

    /// Create a validation result with a single error.
    pub fn error(msg: impl Into<String>) -> Self {
        Self {
            is_valid: false,
            warnings: Vec::new(),
            errors: vec![msg.into()],
        }
    }

    /// Add a warning to the result.
    #[must_use]
    pub fn with_warning(self, msg: impl Into<String>) -> Self {
        Self {
            warnings: self
                .warnings
                .into_iter()
                .chain(std::iter::once(msg.into()))
                .collect(),
            is_valid: self.is_valid,
            errors: self.errors,
        }
    }

    /// Merge another validation result into this one.
    #[must_use]
    pub fn merge(self, other: Self) -> Self {
        Self {
            is_valid: self.is_valid && other.is_valid,
            warnings: self.warnings.into_iter().chain(other.warnings).collect(),
            errors: self.errors.into_iter().chain(other.errors).collect(),
        }
    }
}

/// Validate a checkpoint before resuming.
///
/// Performs comprehensive validation to ensure the checkpoint can be safely resumed:
/// - Working directory matches
/// - PROMPT.md hasn't changed (if checksum available)
/// - Agent configurations are compatible
///
/// Note: File system state validation is handled separately with recovery strategy
/// in the resume flow (see `validate_file_system_state_with_strategy`).
///
/// # Arguments
///
/// * `checkpoint` - The checkpoint to validate
/// * `current_config` - Current configuration to compare against
/// * `registry` - Agent registry for agent validation
/// * `workspace` - Workspace for explicit path resolution
///
/// # Returns
///
/// A `ValidationResult` with any warnings or errors found.
pub fn validate_checkpoint(
    checkpoint: &PipelineCheckpoint,
    current_config: &Config,
    registry: &AgentRegistry,
    workspace: &dyn Workspace,
) -> ValidationResult {
    [
        validate_working_directory(checkpoint, workspace),
        validate_prompt_md(checkpoint, workspace),
        validate_agent_config(
            &checkpoint.developer_agent_config,
            &checkpoint.developer_agent,
            registry,
        ),
        validate_agent_config(
            &checkpoint.reviewer_agent_config,
            &checkpoint.reviewer_agent,
            registry,
        ),
        validate_iteration_counts(checkpoint, current_config),
    ]
    .into_iter()
    .fold(ValidationResult::ok(), |acc, v| acc.merge(v))
}

/// Validate that the working directory matches the checkpoint.
///
/// Uses the workspace root for current working directory comparison.
/// Rejects legacy checkpoints that have no working directory.
pub fn validate_working_directory(
    checkpoint: &PipelineCheckpoint,
    workspace: &dyn Workspace,
) -> ValidationResult {
    if checkpoint.working_dir.is_empty() {
        return ValidationResult::error(
            "Checkpoint has no working directory recorded. Legacy checkpoints are not supported. \
             Delete the checkpoint and restart the pipeline."
                .to_string(),
        );
    }

    let current_dir = workspace.root().to_string_lossy().to_string();

    if current_dir != checkpoint.working_dir {
        return ValidationResult::error(format!(
            "Working directory mismatch: checkpoint was created in '{}', but current directory is '{}'",
            checkpoint.working_dir, current_dir
        ));
    }

    ValidationResult::ok()
}

/// Validate that PROMPT.md hasn't changed since checkpoint.
///
/// Rejects legacy checkpoints that have no PROMPT.md checksum.
pub fn validate_prompt_md(
    checkpoint: &PipelineCheckpoint,
    workspace: &dyn Workspace,
) -> ValidationResult {
    let Some(ref saved_checksum) = checkpoint.prompt_md_checksum else {
        return ValidationResult::error(
            "Checkpoint has no PROMPT.md checksum. Legacy checkpoints are not supported. \
             Delete the checkpoint and restart the pipeline."
                .to_string(),
        );
    };

    let current_checksum =
        calculate_file_checksum_with_workspace(workspace, Path::new("PROMPT.md"));

    match current_checksum {
        Some(current) if current == *saved_checksum => ValidationResult::ok(),
        Some(current) => ValidationResult::ok().with_warning(format!(
            "PROMPT.md has changed since checkpoint was created (checksum: {} -> {})",
            &saved_checksum[..8],
            &current[..8]
        )),
        None => ValidationResult::ok()
            .with_warning("PROMPT.md not found or unreadable - cannot verify integrity"),
    }
}

/// Validate that an agent configuration matches the current registry.
///
/// Rejects legacy checkpoints that have empty agent commands.
#[must_use]
pub fn validate_agent_config(
    saved_config: &AgentConfigSnapshot,
    agent_name: &str,
    registry: &AgentRegistry,
) -> ValidationResult {
    // Reject legacy checkpoints with empty commands
    if saved_config.cmd.is_empty() {
        return ValidationResult::error(format!(
            "Checkpoint has empty agent command for '{agent_name}'. Legacy checkpoints are not supported. \
             Delete the checkpoint and restart the pipeline."
        ));
    }

    let Some(current_config) = registry.resolve_config(agent_name) else {
        return ValidationResult::ok().with_warning(format!(
            "Agent '{agent_name}' not found in current registry (may have been removed)"
        ));
    };

    [
        if current_config.cmd != saved_config.cmd {
            Some(ValidationResult::ok().with_warning(format!(
                "Agent '{}' command changed: '{}' -> '{}'",
                agent_name, saved_config.cmd, current_config.cmd
            )))
        } else {
            None
        },
        if current_config.output_flag != saved_config.output_flag {
            Some(ValidationResult::ok().with_warning(format!(
                "Agent '{}' output flag changed: '{}' -> '{}'",
                agent_name, saved_config.output_flag, current_config.output_flag
            )))
        } else {
            None
        },
        if current_config.can_commit != saved_config.can_commit {
            Some(ValidationResult::ok().with_warning(format!(
                "Agent '{}' can_commit flag changed: {} -> {}",
                agent_name, saved_config.can_commit, current_config.can_commit
            )))
        } else {
            None
        },
    ]
    .into_iter()
    .flatten()
    .fold(ValidationResult::ok(), |acc, v| acc.merge(v))
}

/// Validate iteration counts between checkpoint and current config.
///
/// This is a soft validation - mismatches generate warnings but don't block resume.
/// The checkpoint values take precedence during resume.
#[must_use]
pub fn validate_iteration_counts(
    checkpoint: &PipelineCheckpoint,
    current_config: &Config,
) -> ValidationResult {
    let saved_dev_iters = checkpoint.cli_args.developer_iters;
    let saved_rev_reviews = checkpoint.cli_args.reviewer_reviews;

    [
        if saved_dev_iters > 0 && saved_dev_iters != current_config.developer_iters {
            Some(ValidationResult::ok().with_warning(format!(
                "Developer iterations changed: {} (checkpoint) vs {} (current config). Using checkpoint value.",
                saved_dev_iters, current_config.developer_iters
            )))
        } else { None },
        if saved_rev_reviews > 0 && saved_rev_reviews != current_config.reviewer_reviews {
            Some(ValidationResult::ok().with_warning(format!(
                "Reviewer reviews changed: {} (checkpoint) vs {} (current config). Using checkpoint value.",
                saved_rev_reviews, current_config.reviewer_reviews
            )))
        } else { None },
    ]
    .into_iter()
    .flatten()
    .fold(ValidationResult::ok(), |acc, v| acc.merge(v))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::checkpoint::state::{CheckpointParams, CliArgsSnapshot, PipelinePhase, RebaseState};
    use crate::workspace::MemoryWorkspace;

    fn make_test_checkpoint() -> PipelineCheckpoint {
        let cli_args = CliArgsSnapshot::new(5, 2, None, true, 2, false, None);
        let dev_config =
            AgentConfigSnapshot::new("claude".into(), "claude".into(), "-p".into(), None, true);
        let rev_config =
            AgentConfigSnapshot::new("codex".into(), "codex".into(), "-p".into(), None, true);
        let run_id = uuid::Uuid::new_v4().to_string();

        PipelineCheckpoint::from_params(CheckpointParams {
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
            run_id: &run_id,
            parent_run_id: None,
            resume_count: 0,
            actual_developer_runs: 2,
            actual_reviewer_runs: 0,
            working_dir: "/test/repo".to_string(),
            prompt_md_checksum: None,
            config_path: None,
            config_checksum: None,
        })
    }

    #[test]
    fn test_validation_result_ok() {
        let result = ValidationResult::ok();
        assert!(result.is_valid);
        assert!(result.warnings.is_empty());
        assert!(result.errors.is_empty());
    }

    #[test]
    fn test_validation_result_error() {
        let result = ValidationResult::error("test error");
        assert!(!result.is_valid);
        assert!(result.warnings.is_empty());
        assert_eq!(result.errors.len(), 1);
        assert_eq!(result.errors[0], "test error");
    }

    #[test]
    fn test_validation_result_with_warning() {
        let result = ValidationResult::ok().with_warning("test warning");
        assert!(result.is_valid);
        assert_eq!(result.warnings.len(), 1);
        assert_eq!(result.warnings[0], "test warning");
    }

    #[test]
    fn test_validation_result_merge() {
        let result1 = ValidationResult::ok().with_warning("warning 1");
        let result2 = ValidationResult::ok().with_warning("warning 2");

        let merged = result1.merge(result2);
        assert!(merged.is_valid);
        assert_eq!(merged.warnings.len(), 2);
    }

    #[test]
    fn test_validation_result_merge_with_error() {
        let result1 = ValidationResult::ok();
        let result2 = ValidationResult::error("error");

        let merged = result1.merge(result2);
        assert!(!merged.is_valid);
        assert_eq!(merged.errors.len(), 1);
    }

    #[test]
    fn test_validate_working_directory_empty_rejects_legacy() {
        let checkpoint = PipelineCheckpoint {
            working_dir: String::new(),
            ..make_test_checkpoint()
        };
        let workspace = MemoryWorkspace::new_test();

        let result = validate_working_directory(&checkpoint, &workspace);
        assert!(
            !result.is_valid,
            "Empty working_dir should reject legacy checkpoint"
        );
        assert_eq!(result.errors.len(), 1);
        assert!(result.errors[0].contains("Legacy checkpoints are not supported"));
    }

    #[test]
    fn test_validate_working_directory_mismatch() {
        let checkpoint = PipelineCheckpoint {
            working_dir: "/some/other/directory".to_string(),
            ..make_test_checkpoint()
        };
        let workspace = MemoryWorkspace::new_test();

        let result = validate_working_directory(&checkpoint, &workspace);
        assert!(
            !result.is_valid,
            "Should fail validation on working_dir mismatch"
        );
        assert_eq!(result.errors.len(), 1);
        assert!(result.errors[0].contains("Working directory mismatch"));
    }

    #[test]
    fn test_validate_prompt_md_no_checksum_rejects_legacy() {
        let checkpoint = PipelineCheckpoint {
            prompt_md_checksum: None,
            ..make_test_checkpoint()
        };
        let workspace = MemoryWorkspace::new_test();

        let result = validate_prompt_md(&checkpoint, &workspace);
        assert!(
            !result.is_valid,
            "Missing PROMPT.md checksum should reject legacy checkpoint"
        );
        assert_eq!(result.errors.len(), 1);
        assert!(result.errors[0].contains("Legacy checkpoints are not supported"));
    }
}
