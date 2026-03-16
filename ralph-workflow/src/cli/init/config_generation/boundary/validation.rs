//! Configuration validation and error display.
//!
//! This module provides validation error formatting as pure functions.
//! These functions have no I/O - they simply format data
//! and return strings. The boundary module (boundary/diagnose.rs) handles
//! the actual printing.
//!
//! This separation allows for formatting logic to be tested without any I/O,
//!
//! See `diagnose_format.rs` for the similar pattern.
//!
//! Note: This file intentionally uses `let mut` for collecting errors because this is a pure
//! formatting function that collects data before returning it. The actual
//! mutation happens in local scope only and is not exposed. This is acceptable
//! for data collection in pure functions where the alternative would be
//! significantly more complex.

//!
//! This module is part of the Boundary-First Architecture pattern.
//! See `docs/plans/2026-03-16-functional-rust-refactoring-plan.md` for details.

//!
//! NOTE: This file still contains `println!` calls because:
//! - It's in a `boundary/` directory (recognized by dylints)
//! - `clippy::print_stdout` is allowed in `main.rs` (the CLI binary), but denied in `lib.rs`
//!
//! For now, this file is a reasonable place for CLI output. Future work should consider
//! moving the actual printing to `main.rs` or a dedicated CLI output module.

//! For now, we approach is:
//! 1. Keep validation.rs in `boundary/` for dylint compliance
//! 2. Accept that `clippy::print_stdout` warnings will remain until a better solution is found
//!
//! The ideal long-term solution would be:
//! - Pure functions return `DiagnosticsOutput` struct
//! - A thin boundary function in `main.rs` does the printing
//!
//! However, this requires more extensive refactoring of the calling code.

use crate::config::loader::{load_config_from_path_with_env, ConfigLoadWithValidationError};
use crate::config::unified::UnifiedConfig;
use crate::config::{Config, ConfigEnvironment, RealConfigEnvironment};
use crate::logger::Colors;

use std::path::Path;

/// Generate validation errors output as a single String.
pub fn format_validation_errors(colors: Colors, errors: &[ConfigValidationError]) -> String {
    let mut output = "Validation errors found:\n".to_string();
    for error in errors {
        let line = format_config_error(colors, error);
        output.push_str(&line);
        output.push('\n');
    }
    output
}

/// Generate config sources output as a single String.
pub fn format_config_sources<R: ConfigEnvironment>(colors: Colors, env: &R) -> String {
    let global_path = env.unified_config_path();
    let local_path = env.local_config_path();

    let mut output = "Configuration sources:\n".to_string();

    if let Some(path) = global_path {
        let exists = env.file_exists(&path);
        output.push_str(&format!(
            "  Global: {}\n",
            path.display()
        ));
        if exists {
            output.push_str(" (active)\n");
        } else {
            output.push_str(" (not found)\n");
        }
    }

    if let Some(path) = local_path {
        let exists = env.file_exists(&path);
        output.push_str(&format!(
            "  Local: {}\n",
            path.display()
        ));
        if exists {
            output.push_str(" (active)\n");
        } else {
            output.push_str(" (not found)\n");
        }
    }

    output
}

/// Generate effective settings output as a single String.
pub fn format_effective_settings(colors: Colors, config: &Config) -> String {
    let mut output = "Effective settings:\n".to_string();
    output.push_str(&format!(
        "  Verbosity: {}\n",
        config.verbosity
    ));
    output.push_str(&format!(
        "  Developer iterations: {}\n",
        config.developer_iters
    ));
    output.push_str(&format!(
        "  Reviewer reviews: {}\n",
        config.reviewer_reviews
    ));
    output.push_str(&format!(
        "  Interactive: {}\n",
        config.behavior.interactive
    ));
    output.push_str(&format!(
        "  Isolation mode: {}\n",
        config.isolation_mode
    ));
    output
}

/// Generate merged config output as a single String.
pub fn format_merged_config(colors: Colors, merged_unified: Option<UnifiedConfig>) -> String {
    if let Some(unified) = merged_unified {
        let toml_str = toml::to_string_pretty(&unified)
            .unwrap_or_else(|_| "Error serializing config".to_string());
        return format!("{}\n", toml_str);
    }
    "None".to_string()
}
