//! CLI command handlers.
//!
//! Contains handler functions for CLI commands like --list-agents,
//! --diagnose, and --dry-run.
//!
//! # Module Structure
//!
//! - [`baseline`]: Baseline state display commands
//! - [`boundary`]: I/O boundary module for console output handlers
//! - [`dry_run`]: Validation without running agents
//! - [`list`]: Agent listing commands
//! - [`template_mgmt`]: Template management commands (validate, list, show, variables, render)
//! - [`template_selection`]: Interactive template selection when PROMPT.md is missing

pub mod baseline;
#[path = "boundary.rs"]
pub mod boundary;
pub mod dry_run;
pub mod list;
pub mod template_mgmt;

// Re-export handlers at module level for convenience
pub use baseline::handle_show_baseline;
pub use boundary::handle_diagnose;
pub use boundary::{create_prompt_from_template, prompt_template_selection};
pub use dry_run::handle_dry_run;
pub use list::{handle_list_agents, handle_list_available_agents};
pub use template_mgmt::handle_template_commands;
