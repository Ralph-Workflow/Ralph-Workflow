//! CLI argument parsing and command-line interface definitions.
//!
//! This module contains all CLI-related types and functions:
//! - [`Args`] struct with clap configuration for command-line argument parsing
//! - `Preset` enum for preset agent configurations
//! - CLI handler functions for `--list-agents`, `--list-providers`, `--diagnose`
//! - Config initialization handlers for `--init`, `--init-global`
//! - Interactive template selection for PROMPT.md creation
//!
//! # Module Structure
//!
//! - `args` - Args struct with clap configuration
//! - [`presets`] - Preset enum and `apply_args_to_config`
//! - `providers` - Provider listing and info display
//! - `handlers` - Command handlers (list-agents, diagnose, dry-run, template-selection)
//! - `init` - Config initialization handlers (--init, --init-global)

mod args;
mod completions;
mod diagnostics_domain;
mod handlers;
mod init;
pub mod presets;
mod providers;
pub mod reducer;

// Re-export public items for API convenience
pub use args::Args;
pub use args::PauseOnExitMode;
pub use completions::handle_generate_completion;
pub use handlers::boundary::diagnose::ConfigInfo;
pub use handlers::{
    create_prompt_from_template, handle_diagnose, handle_dry_run, handle_list_agents,
    handle_list_available_agents, handle_show_baseline, handle_template_commands,
    prompt_template_selection,
};
pub use init::{
    handle_check_config, handle_check_config_with, handle_extended_help, handle_init_global,
    handle_init_global_with, handle_init_local_config, handle_init_local_config_with,
    handle_list_work_guides, handle_smart_init, handle_smart_init_with,
};
pub use presets::apply_args_to_config;
pub use providers::handle_list_providers;
