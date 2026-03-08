use super::super::validation::ConfigValidationError;
use std::fmt::Write;

/// Error type for config loading with validation.
#[derive(Debug, thiserror::Error)]
pub enum ConfigLoadWithValidationError {
    #[error("Configuration validation failed")]
    ValidationErrors(Vec<ConfigValidationError>),
    #[error("Failed to read config file: {0}")]
    Io(#[from] std::io::Error),
}

impl ConfigLoadWithValidationError {
    /// Format all validation errors for user display.
    #[must_use]
    pub fn format_errors(&self) -> String {
        match self {
            Self::ValidationErrors(errors) => {
                let mut output =
                    String::from("Error: Configuration invalid - cannot start Ralph\n\n");

                // Group errors by file for clearer presentation
                let mut global_errors: Vec<&ConfigValidationError> = Vec::new();
                let mut local_errors: Vec<&ConfigValidationError> = Vec::new();
                let mut other_errors: Vec<&ConfigValidationError> = Vec::new();

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
                    output.push_str("~/.config/ralph-workflow.toml:\n");
                    for error in global_errors {
                        writeln!(output, "  {}", format_single_error(error)).unwrap();
                    }
                    output.push('\n');
                }

                if !local_errors.is_empty() {
                    output.push_str(".agent/ralph-workflow.toml:\n");
                    for error in local_errors {
                        writeln!(output, "  {}", format_single_error(error)).unwrap();
                    }
                    output.push('\n');
                }

                if !other_errors.is_empty() {
                    for error in other_errors {
                        write!(
                            output,
                            "{}:\n  {}\n",
                            error.file().display(),
                            format_single_error(error)
                        )
                        .unwrap();
                    }
                    output.push('\n');
                }

                output.push_str(
                    "Fix these errors and try again, or run `ralph --check-config` for details.",
                );
                output
            }
            Self::Io(e) => e.to_string(),
        }
    }
}

/// Format a single validation error for display.
fn format_single_error(error: &ConfigValidationError) -> String {
    match error {
        ConfigValidationError::TomlSyntax { error, .. } => {
            format!("TOML syntax error: {error}")
        }
        ConfigValidationError::UnknownKey {
            key, suggestion, ..
        } => suggestion.as_ref().map_or_else(
            || format!("Unknown key '{key}'"),
            |s| format!("Unknown key '{key}'. Did you mean '{s}'?"),
        ),
        ConfigValidationError::InvalidValue { key, message, .. } => {
            format!("Invalid value for '{key}': {message}")
        }
    }
}

impl ConfigValidationError {
    /// Get the file path from the error.
    #[must_use]
    pub fn file(&self) -> &std::path::Path {
        match self {
            Self::TomlSyntax { file, .. }
            | Self::InvalidValue { file, .. }
            | Self::UnknownKey { file, .. } => file,
        }
    }
}
