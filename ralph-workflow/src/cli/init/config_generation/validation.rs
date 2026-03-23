//! Configuration validation and error display.
//!
//! Handles `--check-config` flag to validate config files and display effective settings.

use crate::config::loader::{load_config_from_path_with_env, ConfigLoadWithValidationError};
use crate::config::unified::UnifiedConfig;
use crate::config::validation::ConfigValidationError;
use crate::config::{Config, ConfigEnvironment, RealConfigEnvironment};
use crate::logger::Colors;

trait StdIoWriteCompat {
    fn write_fmt(&mut self, args: std::fmt::Arguments<'_>) -> std::io::Result<()>;
}

impl<T: std::io::Write> StdIoWriteCompat for T {
    fn write_fmt(&mut self, args: std::fmt::Arguments<'_>) -> std::io::Result<()> {
        std::io::Write::write_fmt(self, args)
    }
}

fn print_validation_errors(colors: Colors, errors: &[ConfigValidationError]) {
    let _ = writeln!(
        std::io::stdout(),
        "{}Validation errors found:{}",
        colors.red(),
        colors.reset()
    );
    let _ = writeln!(std::io::stdout());

    let global_errors: Vec<&ConfigValidationError> = errors
        .iter()
        .filter(|e| e.file().to_string_lossy().contains(".config"))
        .collect();

    let local_errors: Vec<&ConfigValidationError> = errors
        .iter()
        .filter(|e| e.file().to_string_lossy().contains(".agent"))
        .collect();

    let other_errors: Vec<&ConfigValidationError> = errors
        .iter()
        .filter(|e| {
            let path = e.file().to_string_lossy();
            !path.contains(".config") && !path.contains(".agent")
        })
        .collect();

    if !global_errors.is_empty() {
        let _ = writeln!(
            std::io::stdout(),
            "{}~/.config/ralph-workflow.toml:{}",
            colors.yellow(),
            colors.reset()
        );
        global_errors
            .iter()
            .for_each(|error| print_config_error(colors, error));
        let _ = writeln!(std::io::stdout());
    }

    if !local_errors.is_empty() {
        let _ = writeln!(
            std::io::stdout(),
            "{}.agent/ralph-workflow.toml:{}",
            colors.yellow(),
            colors.reset()
        );
        local_errors
            .iter()
            .for_each(|error| print_config_error(colors, error));
        let _ = writeln!(std::io::stdout());
    }

    if !other_errors.is_empty() {
        other_errors.iter().for_each(|error| {
            let _ = writeln!(
                std::io::stdout(),
                "{}{}:{}",
                colors.yellow(),
                error.file().display(),
                colors.reset()
            );
            print_config_error(colors, error);
            let _ = writeln!(std::io::stdout());
        });
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
        warnings.iter().for_each(|warning| {
            let _ = writeln!(std::io::stdout(), "  {warning}");
        });
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
