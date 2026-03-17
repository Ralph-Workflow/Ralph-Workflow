//! Configuration validation and error display.
//!
//! Handles `--check-config` flag to validate config files and display effective settings.

use crate::config::loader::{load_config_from_path_with_env, ConfigLoadWithValidationError};
use crate::config::unified::UnifiedConfig;
use crate::config::validation::ConfigValidationError;
use crate::config::{Config, ConfigEnvironment, RealConfigEnvironment};
use crate::logger::Colors;
use std::io::Write;

fn print_validation_errors(colors: Colors, errors: &[ConfigValidationError]) {
    let _ = writeln!(
        std::io::stdout(),
        "{}Validation errors found:{}",
        colors.red(),
        colors.reset()
    );
    let _ = writeln!(std::io::stdout());

    // Group errors by file for clearer presentation
    let mut global_errors: Vec<_> = Vec::new();
    let mut local_errors: Vec<_> = Vec::new();
    let mut other_errors: Vec<_> = Vec::new();

    for error in errors {
        let path_str = error.file().to_string_lossy();
        if path_str.contains(".config") {
            global_errors.push(error);
        } else if path_str.contains(".agent") {
            local_errors.push(error);
        } else {
            other_errors.push(error);
        }
    }

    if !global_errors.is_empty() {
        let _ = writeln!(
            std::io::stdout(),
            "{}~/.config/ralph-workflow.toml:{}",
            colors.yellow(),
            colors.reset()
        );
        for error in global_errors {
            print_config_error(colors, error);
        }
        let _ = writeln!(std::io::stdout());
    }

    if !local_errors.is_empty() {
        let _ = writeln!(
            std::io::stdout(),
            "{}.agent/ralph-workflow.toml:{}",
            colors.yellow(),
            colors.reset()
        );
        for error in local_errors {
            print_config_error(colors, error);
        }
        let _ = writeln!(std::io::stdout());
    }

    if !other_errors.is_empty() {
        for error in other_errors {
            let _ = writeln!(
                std::io::stdout(),
                "{}{}:{}",
                colors.yellow(),
                error.file().display(),
                colors.reset()
            );
            print_config_error(colors, error);
            let _ = writeln!(std::io::stdout());
        }
    }

    let _ = writeln!(
        std::io::stdout(),
        "{}Fix these errors and try again.{}",
        colors.red(),
        colors.reset()
    );
}

fn print_config_sources<R: ConfigEnvironment>(colors: Colors, env: &R) {
    let global_path = env.unified_config_path();
    let local_path = env.local_config_path();

    let _ = writeln!(
        std::io::stdout(),
        "{}Configuration sources:{}",
        colors.cyan(),
        colors.reset()
    );

    if let Some(path) = global_path {
        let exists = env.file_exists(&path);
        let _ = writeln!(
            std::io::stdout(),
            "  Global: {} {}",
            path.display(),
            if exists {
                format!("{}(active){}", colors.green(), colors.reset())
            } else {
                format!("{}(not found){}", colors.dim(), colors.reset())
            }
        );
    }

    if let Some(path) = local_path {
        let exists = env.file_exists(&path);
        let _ = writeln!(
            std::io::stdout(),
            "  Local:  {} {}",
            path.display(),
            if exists {
                format!("{}(active){}", colors.green(), colors.reset())
            } else {
                format!("{}(not found){}", colors.dim(), colors.reset())
            }
        );
    }
}

fn print_effective_settings(colors: Colors, config: &Config) {
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(
        std::io::stdout(),
        "{}Effective settings:{}",
        colors.cyan(),
        colors.reset()
    );
    let _ = writeln!(std::io::stdout(), "  Verbosity: {}", config.verbosity as u8);
    let _ = writeln!(
        std::io::stdout(),
        "  Developer iterations: {}",
        config.developer_iters
    );
    let _ = writeln!(
        std::io::stdout(),
        "  Reviewer reviews: {}",
        config.reviewer_reviews
    );
    let _ = writeln!(
        std::io::stdout(),
        "  Interactive: {}",
        config.behavior.interactive
    );
    let _ = writeln!(
        std::io::stdout(),
        "  Isolation mode: {}",
        config.isolation_mode
    );
}

fn print_merged_config(colors: Colors, merged_unified: Option<UnifiedConfig>) {
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(
        std::io::stdout(),
        "{}Full merged configuration:{}",
        colors.cyan(),
        colors.reset()
    );
    if let Some(unified) = merged_unified {
        let toml_str = toml::to_string_pretty(&unified)
            .unwrap_or_else(|_| "Error serializing config".to_string());
        let _ = writeln!(std::io::stdout(), "{toml_str}");
    }
}

/// Handle the `--check-config` flag with a custom environment.
///
/// Validates all config files and displays effective merged settings.
/// Returns error (non-zero exit) if validation fails.
///
/// # Arguments
///
/// * `colors` - Terminal color configuration for output
/// * `env` - Config environment for path resolution and file operations
/// * `verbose` - Whether to display full merged configuration
///
/// # Returns
///
/// Returns `Ok(true)` if validation succeeded, or an error if validation failed.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_check_config_with<R: ConfigEnvironment>(
    colors: Colors,
    env: &R,
    verbose: bool,
) -> anyhow::Result<bool> {
    let _ = writeln!(
        std::io::stdout(),
        "{}Checking configuration...{}",
        colors.dim(),
        colors.reset()
    );
    let _ = writeln!(std::io::stdout());

    let (config, merged_unified, warnings) = match load_config_from_path_with_env(None, env) {
        Ok(result) => result,
        Err(ConfigLoadWithValidationError::ValidationErrors(errors)) => {
            print_validation_errors(colors, &errors);
            return Err(anyhow::anyhow!("Configuration validation failed"));
        }
        Err(ConfigLoadWithValidationError::Io(e)) => {
            return Err(anyhow::anyhow!("Failed to read config file: {e}"));
        }
    };

    if !warnings.is_empty() {
        let _ = writeln!(
            std::io::stdout(),
            "{}Warnings:{}",
            colors.yellow(),
            colors.reset()
        );
        for warning in &warnings {
            let _ = writeln!(std::io::stdout(), "  {warning}");
        }
        let _ = writeln!(std::io::stdout());
    }

    print_config_sources(colors, env);
    print_effective_settings(colors, &config);

    if verbose {
        print_merged_config(colors, merged_unified);
    }

    let _ = writeln!(std::io::stdout());
    let _ = writeln!(
        std::io::stdout(),
        "{}Configuration valid{}",
        colors.green(),
        colors.reset()
    );

    Ok(true)
}

/// Print a single config validation error with appropriate formatting.
fn print_config_error(colors: Colors, error: &ConfigValidationError) {
    match error {
        ConfigValidationError::TomlSyntax { error, .. } => {
            let _ = writeln!(
                std::io::stdout(),
                "  {}TOML syntax error:{}",
                colors.red(),
                colors.reset()
            );
            let _ = writeln!(std::io::stdout(), "    {error}");
        }
        ConfigValidationError::UnknownKey {
            key, suggestion, ..
        } => {
            let _ = writeln!(
                std::io::stdout(),
                "  {}Unknown key '{}'{}",
                colors.red(),
                key,
                colors.reset()
            );
            if let Some(s) = suggestion {
                let _ = writeln!(
                    std::io::stdout(),
                    "    {}Did you mean '{}'?{}",
                    colors.dim(),
                    s,
                    colors.reset()
                );
            }
        }
        ConfigValidationError::InvalidValue { key, message, .. } => {
            let _ = writeln!(
                std::io::stdout(),
                "  {}Invalid value for '{}'{}",
                colors.red(),
                key,
                colors.reset()
            );
            let _ = writeln!(std::io::stdout(), "    {message}");
        }
    }
}

/// Handle the `--check-config` flag using the default environment.
///
/// Convenience wrapper that uses [`RealConfigEnvironment`] internally.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_check_config(colors: Colors, verbose: bool) -> anyhow::Result<bool> {
    handle_check_config_with(colors, &RealConfigEnvironment, verbose)
}
