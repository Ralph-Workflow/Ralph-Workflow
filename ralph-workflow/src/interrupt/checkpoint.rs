// Interrupt checkpoint: InterruptContext and save_interrupt_checkpoint.
//
// This file contains the data structure holding pipeline state needed to
// persist a checkpoint when the user interrupts with Ctrl+C, and the
// function that writes that checkpoint.

use crate::workspace::Workspace;

/// Context needed to save a checkpoint when interrupted.
///
/// This structure holds references to all the state needed to create
/// a checkpoint when the user interrupts the pipeline with Ctrl+C.
#[derive(Clone)]
pub struct InterruptContext {
    /// Current pipeline phase
    pub phase: crate::checkpoint::PipelinePhase,
    /// Current iteration number
    pub iteration: u32,
    /// Total iterations configured
    pub total_iterations: u32,
    /// Current reviewer pass number
    pub reviewer_pass: u32,
    /// Total reviewer passes configured
    pub total_reviewer_passes: u32,
    /// Run context for tracking execution lineage
    pub run_context: crate::checkpoint::RunContext,
    /// Execution history tracking
    pub execution_history: crate::checkpoint::ExecutionHistory,
    /// Prompt history for deterministic resume
    pub prompt_history: std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
    /// Workspace for checkpoint persistence
    pub workspace: std::sync::Arc<dyn Workspace>,
}

/// Save a checkpoint when the pipeline is interrupted.
///
/// This function persists a checkpoint that records the *current operational phase*
/// and sets `interrupted_by_user=true`.
///
/// We intentionally do NOT overwrite the phase to `Interrupted` because that makes
/// `--resume` terminate immediately in `PipelinePhase::Interrupted`.
///
/// # Arguments
///
/// * `context` - The interrupt context containing the current pipeline state
pub(super) fn save_interrupt_checkpoint(context: &InterruptContext) -> anyhow::Result<()> {
    use crate::checkpoint::state::{
        calculate_file_checksum_with_workspace, AgentConfigSnapshot, CheckpointParams,
        CliArgsSnapshotBuilder, PipelineCheckpoint, RebaseState,
    };
    use crate::checkpoint::{load_checkpoint_with_workspace, save_checkpoint_with_workspace};
    use std::path::Path;

    // Read checkpoint from file if exists, update it with current operational phase
    if let Ok(Some(mut checkpoint)) = load_checkpoint_with_workspace(&*context.workspace) {
        // Update existing checkpoint with current operational phase and progress.
        checkpoint.phase = context.phase;
        checkpoint.iteration = context.iteration;
        checkpoint.total_iterations = context.total_iterations;
        checkpoint.reviewer_pass = context.reviewer_pass;
        checkpoint.total_reviewer_passes = context.total_reviewer_passes;
        checkpoint.actual_developer_runs = context.run_context.actual_developer_runs;
        checkpoint.actual_reviewer_runs = context.run_context.actual_reviewer_runs;
        checkpoint.execution_history = Some(context.execution_history.clone());
        checkpoint.prompt_history = Some(context.prompt_history.clone());

        // Mark this as a user-initiated interrupt (Ctrl+C)
        // This exempts the pipeline from the pre-termination commit safety check
        checkpoint.interrupted_by_user = true;

        save_checkpoint_with_workspace(&*context.workspace, &checkpoint)?;
    } else {
        // No checkpoint exists yet - this is early interruption.
        //
        // We still MUST persist a checkpoint (not just print) so that resume can reliably
        // honor the Ctrl+C exemption via `interrupted_by_user=true`.
        //
        // This checkpoint uses conservative placeholder agent snapshots because we don't
        // have access to Config/AgentRegistry in the signal handler.
        let prompt_md_checksum =
            calculate_file_checksum_with_workspace(&*context.workspace, Path::new("PROMPT.md"))
                .or_else(|| Some("unknown".to_string()));

        let cli_args = CliArgsSnapshotBuilder::new(
            context.total_iterations,
            context.total_reviewer_passes,
            /* review_depth */ None,
            /* isolation_mode */ true,
        )
        .build();

        let developer_agent = "unknown";
        let reviewer_agent = "unknown";
        let developer_agent_config = AgentConfigSnapshot::new(
            developer_agent.to_string(),
            "unknown".to_string(),
            "-o".to_string(),
            None,
            /* can_commit */ true,
        );
        let reviewer_agent_config = AgentConfigSnapshot::new(
            reviewer_agent.to_string(),
            "unknown".to_string(),
            "-o".to_string(),
            None,
            /* can_commit */ true,
        );

        let working_dir = context.workspace.root().to_string_lossy().to_string();
        let mut checkpoint = PipelineCheckpoint::from_params(CheckpointParams {
            phase: context.phase,
            iteration: context.iteration,
            total_iterations: context.total_iterations,
            reviewer_pass: context.reviewer_pass,
            total_reviewer_passes: context.total_reviewer_passes,
            developer_agent,
            reviewer_agent,
            cli_args,
            developer_agent_config,
            reviewer_agent_config,
            rebase_state: RebaseState::default(),
            git_user_name: None,
            git_user_email: None,
            run_id: &context.run_context.run_id,
            parent_run_id: context.run_context.parent_run_id.as_deref(),
            resume_count: context.run_context.resume_count,
            actual_developer_runs: context.run_context.actual_developer_runs,
            actual_reviewer_runs: context.run_context.actual_reviewer_runs,
            working_dir,
            prompt_md_checksum,
            config_path: None,
            config_checksum: None,
        });

        checkpoint.execution_history = Some(context.execution_history.clone());
        checkpoint.prompt_history = Some(context.prompt_history.clone());
        checkpoint.interrupted_by_user = true;

        save_checkpoint_with_workspace(&*context.workspace, &checkpoint)?;
    }

    Ok(())
}
