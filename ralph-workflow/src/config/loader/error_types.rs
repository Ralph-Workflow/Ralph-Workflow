use super::super::validation::ConfigValidationError;

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
                // Group errors by file for clearer presentation using iterator pipeline
                let global_and_rest: (Vec<&ConfigValidationError>, Vec<&ConfigValidationError>) =
                    errors.iter().partition(|error| {
                        let path_str = error.file().to_string_lossy();
                        path_str.contains(".config")
                    });

                let global_errors = global_and_rest.0;
                let local_and_other = global_and_rest.1;

                let local_partition: (Vec<&ConfigValidationError>, Vec<&ConfigValidationError>) =
                    local_and_other.iter().partition(|error| {
                        let path_str = error.file().to_string_lossy();
                        path_str.contains(".agent")
                    });

                let local_errors = local_partition.0;
                let other_errors = local_partition.1;

                // Build output string functionally
                let global_section = if global_errors.is_empty() {
                    String::new()
                } else {
                    let error_lines: String = global_errors
                        .iter()
                        .map(|error| format!("  {}", format_single_error(error)))
                        .collect::<Vec<_>>()
                        .join("\n");
                    format!("~/.config/ralph-workflow.toml:\n{}\n\n", error_lines)
                };

                let local_section = if local_errors.is_empty() {
                    String::new()
                } else {
                    let error_lines: String = local_errors
                        .iter()
                        .map(|error| format!("  {}", format_single_error(error)))
                        .collect::<Vec<_>>()
                        .join("\n");
                    format!(".agent/ralph-workflow.toml:\n{}\n\n", error_lines)
                };

                let other_section = if other_errors.is_empty() {
                    String::new()
                } else {
                    let error_lines: String = other_errors
                        .iter()
                        .map(|error| {
                            format!(
                                "{}:\n  {}\n",
                                error.file().display(),
                                format_single_error(error)
                            )
                        })
                        .collect::<Vec<_>>()
                        .join("\n");
                    format!("{}\n", error_lines)
                };

                format!(
                    "Error: Configuration invalid - cannot start Ralph\n\n{}{}{}{}Fix these errors and try again, or run `ralph --check-config` for details.",
                    global_section,
                    local_section,
                    other_section,
                    if other_errors.is_empty() { "" } else { "\n" }
                )
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
