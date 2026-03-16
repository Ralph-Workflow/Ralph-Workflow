//! Global configuration file creation.
//!
//! Handles `--init-global` flag to create the unified config file at
//! `~/.config/ralph-workflow.toml`.

use crate::config::{ConfigEnvironment, RealConfigEnvironment};
use crate::logger::Colors;

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
        println!(
            "{}Unified config already exists:{} {}",
            colors.yellow(),
            colors.reset(),
            global_path.display()
        );
        println!("Edit the file to customize, or delete it to regenerate from defaults.");
        println!();
        println!("Next steps:");
        println!("  1. Create a PROMPT.md for your task:");
        println!("       ralph --init <work-guide>");
        println!("       ralph --list-work-guides  # Show all Work Guides");
        println!("  2. Or run ralph directly with default settings:");
        println!("       ralph \"your commit message\"");
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

    println!(
        "{}Created unified config: {}{}{}\n",
        colors.green(),
        colors.bold(),
        global_path.display(),
        colors.reset()
    );
    println!("This is the primary configuration file for Ralph.");
    println!();
    println!("Features:");
    println!("  - General settings (verbosity, iterations, etc.)");
    println!("  - CCS aliases for Claude Code Switch integration");
    println!("  - Custom agent definitions");
    println!("  - Agent chain configuration with fallbacks");
    println!();
    println!("Environment variables (RALPH_*) override these settings.");
    println!();
    println!("Next steps:");
    println!("  1. Create a PROMPT.md for your task:");
    println!("       ralph --init <work-guide>");
    println!("       ralph --list-work-guides  # Show all Work Guides");
    println!("  2. Or run ralph directly with default settings:");
    println!("       ralph \"your commit message\"");
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
