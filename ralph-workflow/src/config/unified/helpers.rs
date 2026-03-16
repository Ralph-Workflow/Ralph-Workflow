//! Configuration helper functions and utilities.
//!
//! This module provides utility functions for working with the unified configuration.

use std::path::PathBuf;

use crate::prompts::io::get_xdg_config_home;

/// Default path for the unified configuration file.
pub const DEFAULT_UNIFIED_CONFIG_NAME: &str = "ralph-workflow.toml";

/// Get the path to the unified config file.
///
/// Returns `~/.config/ralph-workflow.toml` by default.
///
/// If `XDG_CONFIG_HOME` is set, uses `{XDG_CONFIG_HOME}/ralph-workflow.toml`.
///
/// # Returns
///
/// - `Some(PathBuf)` with the config path if the home directory can be determined
/// - `None` if the home directory cannot be determined
///
/// # Examples
///
/// ```rust
/// use ralph_workflow::config::unified::unified_config_path;
///
/// if let Some(path) = unified_config_path() {
///     println!("Config path: {}", path.display());
/// }
/// ```
#[must_use]
pub fn unified_config_path() -> Option<PathBuf> {
    if let Some(xdg) = get_xdg_config_home() {
        let xdg = xdg.trim();
        if !xdg.is_empty() {
            return Some(PathBuf::from(xdg).join(DEFAULT_UNIFIED_CONFIG_NAME));
        }
    }

    dirs::home_dir().map(|d| d.join(".config").join(DEFAULT_UNIFIED_CONFIG_NAME))
}
