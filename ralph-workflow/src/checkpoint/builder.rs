//! Checkpoint builder for convenient checkpoint creation.
//!
//! This module provides a builder pattern for creating checkpoints
//! from various contexts in the pipeline.

use crate::agents::AgentRegistry;
use crate::checkpoint::execution_history::ExecutionHistory;
use crate::checkpoint::file_state::FileSystemState;
use crate::checkpoint::state::{
    calculate_file_checksum_with_workspace, AgentConfigSnapshot, CheckpointParams, CliArgsSnapshot,
    PipelineCheckpoint, PipelinePhase, RebaseState,
};
use crate::checkpoint::RunContext;
use crate::config::{Config, ReviewDepth};
use crate::logger::Logger;
use crate::reducer::state::{PromptInputsState, PromptPermissionsState};
use crate::workspace::Workspace;
use crate::ProcessExecutor;
use std::sync::Arc;

/// Builder for creating pipeline checkpoints.
///
/// Provides a convenient interface for capturing all necessary state
/// when creating checkpoints during pipeline execution.
///
/// # Example
///
/// ```ignore
/// let checkpoint = CheckpointBuilder::new()
///     .phase(PipelinePhase::Development, 3, 5)
///     .reviewer_pass(1, 2)
///     .capture_from_config(&ctx, &registry, "claude", "codex")
///     .build();
/// ```
pub struct CheckpointBuilder {
    phase: Option<PipelinePhase>,
    iteration: u32,
    total_iterations: u32,
    reviewer_pass: u32,
    total_reviewer_passes: u32,
    developer_agent: Option<String>,
    reviewer_agent: Option<String>,
    cli_args: Option<CliArgsSnapshot>,
    developer_agent_config: Option<AgentConfigSnapshot>,
    reviewer_agent_config: Option<AgentConfigSnapshot>,
    rebase_state: RebaseState,
    config_path: Option<std::path::PathBuf>,
    git_user_name: Option<String>,
    git_user_email: Option<String>,
    // Run context for tracking execution lineage and state
    run_context: Option<RunContext>,
    // Hardened resume fields
    execution_history: Option<ExecutionHistory>,
    prompt_history: Option<std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>>,
    prompt_inputs: Option<PromptInputsState>,
    prompt_permissions: PromptPermissionsState,
    last_substitution_log: Option<crate::prompts::SubstitutionLog>,
    // Process executor for external process execution
    executor: Option<Arc<dyn ProcessExecutor>>,
    // Logging run_id (timestamp-based) for per-run log directory
    log_run_id: Option<String>,
}

impl Default for CheckpointBuilder {
    fn default() -> Self {
        Self::new()
    }
}

impl CheckpointBuilder {
    /// Create a new checkpoint builder with default values.
    #[must_use]
    pub fn new() -> Self {
        Self {
            phase: None,
            iteration: 1,
            total_iterations: 1,
            reviewer_pass: 0,
            total_reviewer_passes: 0,
            developer_agent: None,
            reviewer_agent: None,
            cli_args: None,
            developer_agent_config: None,
            reviewer_agent_config: None,
            rebase_state: RebaseState::default(),
            config_path: None,
            git_user_name: None,
            git_user_email: None,
            run_context: None,
            execution_history: None,
            prompt_history: None,
            prompt_inputs: None,
            prompt_permissions: PromptPermissionsState::default(),
            last_substitution_log: None,
            executor: None,
            log_run_id: None,
        }
    }

    /// Set the phase and iteration information.
    #[must_use]
    pub fn phase(self, phase: PipelinePhase, iteration: u32, total_iterations: u32) -> Self {
        Self {
            phase: Some(phase),
            iteration,
            total_iterations,
            ..self
        }
    }

    /// Set the reviewer pass information.
    #[must_use]
    pub fn reviewer_pass(self, pass: u32, total: u32) -> Self {
        Self {
            reviewer_pass: pass,
            total_reviewer_passes: total,
            ..self
        }
    }

    /// Set the agent names.
    #[must_use]
    pub fn agents(self, developer: &str, reviewer: &str) -> Self {
        Self {
            developer_agent: Some(developer.to_string()),
            reviewer_agent: Some(reviewer.to_string()),
            ..self
        }
    }

    /// Set the CLI arguments snapshot.
    #[must_use]
    pub fn cli_args(self, args: CliArgsSnapshot) -> Self {
        Self {
            cli_args: Some(args),
            ..self
        }
    }

    /// Set the last template substitution log for validation and observability.
    #[must_use]
    pub fn with_last_substitution_log(self, log: Option<crate::prompts::SubstitutionLog>) -> Self {
        Self {
            last_substitution_log: log,
            ..self
        }
    }

    /// Set the developer agent configuration snapshot.
    #[must_use]
    pub fn developer_config(self, config: AgentConfigSnapshot) -> Self {
        Self {
            developer_agent_config: Some(config),
            ..self
        }
    }

    /// Set the reviewer agent configuration snapshot.
    #[must_use]
    pub fn reviewer_config(self, config: AgentConfigSnapshot) -> Self {
        Self {
            reviewer_agent_config: Some(config),
            ..self
        }
    }

    /// Set the rebase state.
    #[must_use]
    pub fn rebase_state(self, state: RebaseState) -> Self {
        Self {
            rebase_state: state,
            ..self
        }
    }

    /// Set the config path.
    #[must_use]
    pub fn config_path(self, path: Option<std::path::PathBuf>) -> Self {
        Self {
            config_path: path,
            ..self
        }
    }

    /// Set the git user name and email.
    #[must_use]
    pub fn git_identity(self, name: Option<&str>, email: Option<&str>) -> Self {
        Self {
            git_user_name: name.map(String::from),
            git_user_email: email.map(String::from),
            ..self
        }
    }

    /// Set the process executor for external process execution.
    #[must_use]
    pub fn with_executor(self, executor: Arc<dyn ProcessExecutor>) -> Self {
        Self {
            executor: Some(executor),
            ..self
        }
    }

    /// Capture CLI arguments from a Config.
    #[must_use]
    pub fn capture_cli_args(self, config: &Config) -> Self {
        let review_depth_str = Some(review_depth_to_string(config.review_depth).to_string());
        let snapshot = crate::checkpoint::state::CliArgsSnapshotBuilder::new(
            config.developer_iters,
            config.reviewer_reviews,
            review_depth_str,
            config.isolation_mode,
        )
        .verbosity(config.verbosity as u8)
        .show_streaming_metrics(config.show_streaming_metrics)
        .reviewer_json_parser(config.reviewer_json_parser.clone())
        .build();
        Self {
            cli_args: Some(snapshot),
            ..self
        }
    }

    /// Capture all configuration from a `PhaseContext` and `AgentRegistry`.
    ///
    /// This is a convenience method that captures CLI args and both agent configs.
    /// It takes a `PhaseContext` which provides access to config, registry, and agents.
    #[must_use]
    pub fn capture_from_context(
        mut self,
        config: &Config,
        registry: &AgentRegistry,
        developer_name: &str,
        reviewer_name: &str,
        logger: &Logger,
        run_context: &RunContext,
    ) -> Self {
        // Store run context (cloned for builder ownership)
        self.run_context = Some(run_context.clone());

        // Capture CLI args
        self = self.capture_cli_args(config);

        // Capture developer agent config
        if let Some(agent_config) = registry.resolve_config(developer_name) {
            let snapshot = AgentConfigSnapshot::new(
                developer_name.to_string(),
                agent_config.cmd.clone(),
                agent_config.output_flag.clone(),
                Some(agent_config.yolo_flag.clone()),
                agent_config.can_commit,
            )
            .with_model_override(config.developer_model.clone())
            .with_provider_override(config.developer_provider.clone())
            .with_context_level(config.developer_context);
            self.developer_agent_config = Some(snapshot);
            self.developer_agent = Some(developer_name.to_string());
        } else {
            logger.warn(&format!(
                "Developer agent '{developer_name}' not found in registry"
            ));
        }

        // Capture reviewer agent config
        if let Some(agent_config) = registry.resolve_config(reviewer_name) {
            let snapshot = AgentConfigSnapshot::new(
                reviewer_name.to_string(),
                agent_config.cmd.clone(),
                agent_config.output_flag.clone(),
                Some(agent_config.yolo_flag.clone()),
                agent_config.can_commit,
            )
            .with_model_override(config.reviewer_model.clone())
            .with_provider_override(config.reviewer_provider.clone())
            .with_context_level(config.reviewer_context);
            self.reviewer_agent_config = Some(snapshot);
            self.reviewer_agent = Some(reviewer_name.to_string());
        } else {
            logger.warn(&format!(
                "Reviewer agent '{reviewer_name}' not found in registry"
            ));
        }

        // Capture git identity
        self.git_user_name = config.git_user_name.clone();
        self.git_user_email = config.git_user_email.clone();

        self
    }

    /// Set the executor from a `PhaseContext`.
    ///
    /// This is a convenience method that extracts the `executor_arc` from `PhaseContext`
    /// and sets it for the checkpoint builder.
    #[must_use]
    pub fn with_executor_from_context(self, executor_arc: Arc<dyn ProcessExecutor>) -> Self {
        Self {
            executor: Some(executor_arc),
            ..self
        }
    }

    /// Attach execution history from a `PhaseContext`.
    ///
    /// This method captures the execution history from the phase context
    /// and attaches it to the checkpoint.
    #[must_use]
    pub fn with_execution_history(self, history: ExecutionHistory) -> Self {
        Self {
            execution_history: Some(history),
            ..self
        }
    }

    /// Set the entire prompt history from a `HashMap`.
    ///
    /// This is used when building checkpoints from reducer-owned `PipelineState::prompt_history`.
    ///
    /// # Arguments
    ///
    /// * `history` - `HashMap` of prompt keys to `PromptHistoryEntry` values
    #[must_use]
    pub fn with_prompt_history(
        self,
        history: std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
    ) -> Self {
        let prompt_history = if history.is_empty() {
            None
        } else {
            Some(history)
        };
        Self {
            prompt_history,
            ..self
        }
    }

    /// Attach reducer-managed prompt input materialization state.
    ///
    /// This is used by reducer-driven checkpointing so resumes can avoid repeating
    /// oversize handling that was already materialized for a given content id and
    /// consumer signature.
    #[must_use]
    pub fn with_prompt_inputs(self, prompt_inputs: PromptInputsState) -> Self {
        let is_empty = prompt_inputs.planning.is_none()
            && prompt_inputs.development.is_none()
            && prompt_inputs.review.is_none()
            && prompt_inputs.commit.is_none()
            && prompt_inputs.xsd_retry_last_output.is_none();
        let prompt_inputs = if is_empty { None } else { Some(prompt_inputs) };
        Self {
            prompt_inputs,
            ..self
        }
    }

    /// Set prompt permission state for resume-safe restoration.
    #[must_use]
    pub fn with_prompt_permissions(self, prompt_permissions: PromptPermissionsState) -> Self {
        Self {
            prompt_permissions,
            ..self
        }
    }

    /// Set the logging `run_id` (timestamp-based) for per-run log directory.
    ///
    /// This should be set from the `RunLogContext.run_id()` to ensure resume
    /// continuity - when resuming, logs will continue in the same directory.
    #[must_use]
    pub fn with_log_run_id(self, log_run_id: String) -> Self {
        Self {
            log_run_id: Some(log_run_id),
            ..self
        }
    }

    /// Build the checkpoint without workspace.
    ///
    /// Returns None if required fields (phase, agent configs) are missing.
    /// Generates a new `RunContext` if not set.
    ///
    /// This method uses CWD-relative file operations for file state capture.
    /// For pipeline code where a workspace is available, prefer `build_with_workspace()`.
    #[must_use]
    pub fn build(self) -> Option<PipelineCheckpoint> {
        self.build_internal(None)
    }

    /// Build the checkpoint with workspace-aware file capture.
    ///
    /// Returns None if required fields (phase, agent configs) are missing.
    /// Generates a new `RunContext` if not set.
    ///
    /// This method uses the workspace abstraction for file state capture, which is
    /// the preferred approach for pipeline code. The workspace provides:
    /// - Explicit path resolution relative to repo root
    /// - Testability via `MemoryWorkspace` in tests
    pub fn build_with_workspace(self, workspace: &dyn Workspace) -> Option<PipelineCheckpoint> {
        self.build_internal(Some(workspace))
    }

    /// Internal build implementation that handles both workspace and non-workspace cases.
    fn build_internal(self, workspace: Option<&dyn Workspace>) -> Option<PipelineCheckpoint> {
        let phase = self.phase?;
        let developer_agent = self.developer_agent?;
        let reviewer_agent = self.reviewer_agent?;
        let cli_args = self.cli_args?;
        let developer_config = self.developer_agent_config?;
        let reviewer_config = self.reviewer_agent_config?;

        let git_user_name = self.git_user_name.as_deref();
        let git_user_email = self.git_user_email.as_deref();

        // Use provided run context or generate a new one
        let run_context = self.run_context.unwrap_or_default();

        let working_dir = workspace
            .map(|ws| ws.root().to_string_lossy().to_string())
            .or_else(crate::checkpoint::current_dir::get_current_dir)
            .unwrap_or_default();

        let prompt_md_checksum = workspace.and_then(|ws| {
            calculate_file_checksum_with_workspace(ws, std::path::Path::new("PROMPT.md"))
        });

        let (config_path, config_checksum) = self.config_path.map_or((None, None), |path| {
            let path_string = path.to_string_lossy().to_string();
            let checksum = workspace.and_then(|ws| {
                let relative = path.strip_prefix(ws.root()).ok().unwrap_or(&path);
                calculate_file_checksum_with_workspace(ws, relative)
            });
            (Some(path_string), checksum)
        });

        let executor_ref = self.executor.as_ref().map(std::convert::AsRef::as_ref);

        let checkpoint = PipelineCheckpoint {
            execution_history: self.execution_history,
            prompt_history: self.prompt_history,
            prompt_inputs: self.prompt_inputs,
            prompt_permissions: self.prompt_permissions,
            last_substitution_log: self.last_substitution_log,
            log_run_id: self.log_run_id,
            file_system_state: workspace.map_or_else(
                || {
                    Some(FileSystemState::capture_with_optional_executor_impl(
                        executor_ref,
                    ))
                },
                |ws| {
                    executor_ref
                        .map(|executor| FileSystemState::capture_with_workspace(ws, executor))
                },
            ),
            env_snapshot: Some(crate::checkpoint::state::EnvironmentSnapshot::capture_current()),
            ..PipelineCheckpoint::from_params(CheckpointParams {
                phase,
                iteration: self.iteration,
                total_iterations: self.total_iterations,
                reviewer_pass: self.reviewer_pass,
                total_reviewer_passes: self.total_reviewer_passes,
                developer_agent: &developer_agent,
                reviewer_agent: &reviewer_agent,
                cli_args,
                developer_agent_config: developer_config,
                reviewer_agent_config: reviewer_config,
                rebase_state: self.rebase_state,
                git_user_name,
                git_user_email,
                run_id: &run_context.run_id,
                parent_run_id: run_context.parent_run_id.as_deref(),
                resume_count: run_context.resume_count,
                actual_developer_runs: run_context.actual_developer_runs.max(self.iteration),
                actual_reviewer_runs: run_context.actual_reviewer_runs.max(self.reviewer_pass),
                working_dir,
                prompt_md_checksum,
                config_path,
                config_checksum,
            })
        };

        Some(checkpoint)
    }
}

/// Convert `ReviewDepth` to a string representation.
const fn review_depth_to_string(depth: ReviewDepth) -> &'static str {
    match depth {
        ReviewDepth::Standard => "standard",
        ReviewDepth::Comprehensive => "comprehensive",
        ReviewDepth::Security => "security",
        ReviewDepth::Incremental => "incremental",
    }
}

#[cfg(test)]
mod tests;
