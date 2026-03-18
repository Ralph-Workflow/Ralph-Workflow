//! Application entrypoint and pipeline orchestration.
//!
//! This module is the CLI layer operating **before** the repository root is known.
//! It uses [`AppEffect`][effect::AppEffect] for side effects, which is distinct from
//! [`Effect`][crate::reducer::effect::Effect] used after repo root discovery.
//!
//! # Two Effect Layers
//!
//! Ralph has two distinct effect types (see also [`crate`] documentation):
//!
//! | Layer | When | Filesystem Access |
//! |-------|------|-------------------|
//! | `AppEffect` (this module) | Before repo root known | `std::fs` directly |
//! | `Effect` ([`crate::reducer`]) | After repo root known | Via [`Workspace`][crate::workspace::Workspace] |
//!
//! These layers must never mix: `AppEffect` handlers cannot use `Workspace`.
//!
//! # Responsibilities
//!
//! - CLI/config parsing and plumbing commands
//! - Agent registry loading
//! - Repo root discovery
//! - Resume support and checkpoint management
//! - Transition to pipeline execution via `crate::phases`
//!
//! # Module Structure
//!
//! - [`config_init`]: Configuration loading and agent registry initialization
//! - [`effect`]: `AppEffect` definitions for pre-repo-root operations
//! - [`effect_handler`]: Production handler for `AppEffect` execution
//! - [`plumbing`]: Low-level git operations (show/apply commit messages)
//! - [`validation`]: Agent validation and chain validation
//! - [`resume`]: Checkpoint resume functionality
//! - [`detection`]: Project stack detection
//! - [`finalization`]: Pipeline cleanup and finalization

use crate::agents::AgentRegistry;
use crate::app::effect_handler::RealAppEffectHandler;
use crate::app::finalization::finalize_pipeline;
use crate::banner::print_welcome_banner;
use crate::checkpoint::{
    save_checkpoint_with_workspace, CheckpointBuilder, PipelineCheckpoint, PipelinePhase,
};
use crate::cli::{
    create_prompt_from_template, handle_diagnose, handle_dry_run, handle_list_agents,
    handle_list_available_agents, handle_list_providers, handle_show_baseline,
    handle_template_commands, prompt_template_selection, Args,
};

use crate::executor::ProcessExecutor;
use crate::files::protection::monitoring::PromptMonitor;
use crate::files::{
    create_prompt_backup_with_workspace, update_status_with_workspace,
    validate_prompt_md_with_workspace,
};
use crate::git_helpers::{
    abort_rebase, continue_rebase, get_conflicted_files, is_main_or_master_branch, RebaseResult,
};
use crate::logger::Colors;
use crate::logger::Logger;
use crate::phases::PhaseContext;
use crate::pipeline::{AgentPhaseGuard, Timer};
use crate::prompts::template_context::TemplateContext;

use crate::app::config_init::initialize_config;
use crate::app::context::PipelineContext;
use crate::app::detection::detect_project_stack;
use crate::app::rebase::{run_rebase_to_default, try_resolve_conflicts_without_phase_ctx};
use crate::app::resume::{handle_resume_with_validation, offer_resume_if_checkpoint_exists};
use crate::app::validation::{
    resolve_required_agents, validate_agent_chains, validate_agent_commands, validate_can_commit,
};

// Include sub-modules
pub mod command_handlers;
pub mod pipeline_execution;
pub mod setup_helpers;
#[cfg(test)]
pub mod tests;

// Re-exports from pipeline_execution
pub use pipeline_execution::run;

// Re-exports from pipeline_execution (helpers is included via include!)
pub use pipeline_execution::CommandExitCleanupGuard;

// Re-exports from pipeline_execution (initialization is included via include!)
pub use pipeline_execution::PipelinePreparationParams;

// Re-exports from setup_helpers
pub use setup_helpers::{validate_and_setup_agents, AgentSetupParams};
