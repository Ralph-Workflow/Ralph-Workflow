//! Global configuration file creation.
//!
//! Handles `--init-global` flag to create the unified config file at
//! `~/.config/ralph-workflow.toml`.

use crate::config::{ConfigEnvironment, RealConfigEnvironment};
use crate::logger::Colors;
use std::io::Write;

/// Handle the `--init-global` flag with a custom path resolver.
///
/// Creates a unified config file at the path determined by the resolver.
/// This is the recommended way to configure Ralph globally.
///
/// # Arguments
///
/// * `colors` - Terminal color configuration for output
/// * `env` - Path resolver for determining config file location
///
/// # Returns
///
/// Returns `Ok(true)` if the flag was handled (program should exit after),
/// or an error if config creation failed.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_init_global_with<R: ConfigEnvironment>(
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    let global_path = env
        .unified_config_path()
        .ok_or_else(|| anyhow::anyhow!("Cannot determine config directory (no home directory)"))?;

    // Check if config already exists using the environment
    if env.file_exists(&global_path) {
        let _ = writeln!(
            std::io::stdout(),
            "{}Unified config already exists:{} {}",
            colors.yellow(),
            colors.reset(),
            global_path.display()
        );
        let _ = writeln!(
            std::io::stdout(),
            "Edit the file to customize, or delete it to regenerate from defaults."
        );
        let _ = writeln!(std::io::stdout());
        let _ = writeln!(std::io::stdout(), "Next steps:");
        let _ = writeln!(std::io::stdout(), "  1. Create a PROMPT.md for your task:");
        let _ = writeln!(std::io::stdout(), "       ralph --init <work-guide>");
        let _ = writeln!(
            std::io::stdout(),
            "       ralph --list-work-guides  # Show all Work Guides"
        );
        let _ = writeln!(
            std::io::stdout(),
            "  2. Or run ralph directly with default settings:"
        );
        let _ = writeln!(std::io::stdout(), "       ralph \"your commit message\"");
        return Ok(true);
    }

    // Create config using the environment's file operations
    env.write_file(&global_path, crate::config::unified::DEFAULT_UNIFIED_CONFIG)
        .map_err(|e| {
            anyhow::anyhow!(
                "Failed to create config file {}: {}",
                global_path.display(),
                e
            )
        })?;

    let _ = writeln!(
        std::io::stdout(),
        "{}Created unified config: {}{}{}\n",
        colors.green(),
        colors.bold(),
        global_path.display(),
        colors.reset()
    );
    let _ = writeln!(
        std::io::stdout(),
        "This is the primary configuration file for Ralph."
    );
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(std::io::stdout(), "Features:");
    let _ = writeln!(
        std::io::stdout(),
        "  - General settings (verbosity, iterations, etc.)"
    );
    let _ = writeln!(
        std::io::stdout(),
        "  - CCS aliases for Claude Code Switch integration"
    );
    let _ = writeln!(std::io::stdout(), "  - Custom agent definitions");
    let _ = writeln!(
        std::io::stdout(),
        "  - Agent chain configuration with fallbacks"
    );
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(
        std::io::stdout(),
        "Environment variables (RALPH_*) override these settings."
    );
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(std::io::stdout(), "Next steps:");
    let _ = writeln!(std::io::stdout(), "  1. Create a PROMPT.md for your task:");
    let _ = writeln!(std::io::stdout(), "       ralph --init <work-guide>");
    let _ = writeln!(
        std::io::stdout(),
        "       ralph --list-work-guides  # Show all Work Guides"
    );
    let _ = writeln!(
        std::io::stdout(),
        "  2. Or run ralph directly with default settings:"
    );
    let _ = writeln!(std::io::stdout(), "       ralph \"your commit message\"");
    Ok(true)
}

/// Handle the `--init-global` flag using the default path resolver.
///
/// Creates a unified config file at `~/.config/ralph-workflow.toml` if it doesn't exist.
/// This is a convenience wrapper that uses [`RealConfigEnvironment`] internally.
///
/// # Arguments
///
/// * `colors` - Terminal color configuration for output
///
/// # Returns
///
/// Returns `Ok(true)` if the flag was handled (program should exit after),
/// or an error if config creation failed.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_init_global(colors: Colors) -> anyhow::Result<bool> {
    handle_init_global_with(colors, &RealConfigEnvironment)
}
