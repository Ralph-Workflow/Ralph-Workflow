//! I/O module for prompts - contains filesystem-boundary code.
//!
//! This module satisfies the dylint boundary-module check for code that uses
//! std::fs for loading templates.

use std::fs;
use std::io;
use std::path::Path;

/// Load template content from the filesystem.
///
/// This is a thin I/O wrapper that delegates to std::fs.
pub fn load_template(path: &Path) -> io::Result<String> {
    fs::read_to_string(path)
}

/// Check if a template file exists.
pub fn template_exists(path: &Path) -> bool {
    path.exists()
}

/// Read XDG config home from environment.
///
/// This is a thin wrapper around std::env.
pub fn get_xdg_config_home() -> Option<String> {
    std::env::var("XDG_CONFIG_HOME").ok()
}
