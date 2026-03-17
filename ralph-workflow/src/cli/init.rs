//! Configuration initialization handlers.
//!
//! This module handles the `--init` and `--init-global` flags for creating
//! default unified configuration files and PROMPT.md from templates.
//!
//! # Dependency Injection
//!
//! All init handlers accept a [`ConfigEnvironment`] for path resolution, enabling
//! tests to inject custom paths without relying on environment variables.
//!
//! For convenience, wrapper functions without the resolver parameter are provided
//! that use [`RealConfigEnvironment`] internally.

use crate::config::{ConfigEnvironment, RealConfigEnvironment};
use crate::logger::Colors;
use crate::templates::{get_template, list_templates, ALL_TEMPLATES};
use std::io::{IsTerminal, Write};
use std::path::Path;

/// Minimum similarity threshold for suggesting alternatives (0-100 percentage).
const MIN_SIMILARITY_PERCENT: u32 = 40;

// Include project detection logic (Levenshtein distance, similarity, fuzzy matching)
include!("init/project_detection.rs");

// Configuration generation module (handlers for creating config files and PROMPT.md)
mod config_generation;

// Re-export config generation public API
pub use config_generation::{
    handle_check_config, handle_check_config_with, handle_init_global, handle_init_global_with,
    handle_init_local_config, handle_init_local_config_with, handle_init_state_inference_with_env,
    handle_init_template_arg_at_path_with_env,
};

// Include init prompting utilities for interactive flows.
include!("init/prompting.rs");

// Include Work Guide listing for `--list-work-guides`.
include!("init/work_guides.rs");

// Include the smart `--init` orchestration and helpers.
include!("init/smart_init.rs");

// Include `--extended-help` output.
include!("init/extended_help.rs");

#[cfg(test)]
mod tests {
    include!("init/tests.rs");
}
