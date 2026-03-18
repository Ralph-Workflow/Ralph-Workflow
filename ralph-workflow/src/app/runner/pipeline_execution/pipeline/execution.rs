// Pipeline Execution
//
// This module coordinates pipeline execution through three main phases:
// 1. Initialization (initialization.rs): Context preparation and early-exit handling
// 2. Execution (execution_core.rs): Main reducer-based event loop
// 3. Completion (completion.rs): Defensive completion marker writing
//
// Architecture:
//
// The pipeline follows a reducer-based architecture:
// State → Orchestrator → Effect → Handler → Event → Reducer → State
//
// The reducer pattern ensures:
// - Reproducibility: Given same state + events = same result
// - Testability: Pure reducers and orchestrators can be tested without I/O
// - Resumability: State can be checkpointed and restored at any point
// - Observability: Event stream provides audit trail of execution
//
// Module Organization:
//
// - initialization.rs - Pipeline context preparation, checkpoint restoration, early-exit modes
// - execution_core.rs - Main event loop execution with MainEffectHandler
// - completion.rs - Defensive completion marker for abnormal terminations
// - testing.rs - Test entry points with custom effect handlers
//
// Entry Points:
//
// Production:
// - run_pipeline() - Standard entry point, uses MainEffectHandler
// - run_pipeline_with_default_handler() - Direct event loop execution
//
// Testing:
// - run_pipeline_with_effect_handler() - Custom effect handler injection (see testing.rs)
//
// See Also:
//
// - Event loop implementation: app/event_loop/
// - Reducer architecture: docs/architecture/event-loop-and-reducers.md
// - Effect system: docs/architecture/effect-system.md

use anyhow::Context;
use std::io::Write;

use crate::agents::AgentRegistry;
use crate::app::context::PipelineContext;
use crate::app::effect::AppEffectHandler;
use crate::app::effectful;
use crate::checkpoint::{
    load_checkpoint_with_workspace, save_checkpoint_with_workspace, CheckpointBuilder,
    PipelineCheckpoint, PipelinePhase, RunContext,
};
use crate::cli::Args;
use crate::config::Config;
use crate::executor::ProcessExecutor;
use crate::files::protection::monitoring::PromptMonitor;
use crate::files::{
    create_prompt_backup_with_workspace, update_status_with_workspace,
    validate_prompt_md_with_workspace,
};
use crate::git_helpers::{
    abort_rebase, continue_rebase, get_conflicted_files, is_main_or_master_branch, RebaseResult,
};
use crate::logger::{Colors, Logger};
use crate::logging::RunLogContext;
use crate::phases::PhaseContext;
use crate::pipeline::{prepare_agent_phase_for_workspace, AgentPhaseGuard, Timer};
use crate::prompts::template_context::TemplateContext;
use crate::workspace::Workspace;

// Include sub-modules
include!("initialization.rs");
include!("execution_core.rs");
include!("completion.rs");

/// Runs the full development/review/commit pipeline using reducer-based event loop.
///
/// This is the standard production entry point. It:
/// 1. Prepares the pipeline context (via `prepare_pipeline_or_exit`)
/// 2. Runs the event loop (via `run_pipeline_with_default_handler`)
///
/// # Early Exit Conditions
///
/// Returns `Ok(())` without running the pipeline if:
/// - `--dry-run`: Displays configuration only
/// - `--rebase-only`: Runs rebase operation only
/// - `--generate-commit-msg`: Generates commit message only
///
/// # Errors
///
/// Returns error if:
/// - Pipeline initialization fails
/// - Event loop execution fails
/// - Finalization operations fail
fn run_pipeline(ctx: &PipelineContext) -> anyhow::Result<()> {
    // Use MainEffectHandler for production
    run_pipeline_with_default_handler(ctx)
}
