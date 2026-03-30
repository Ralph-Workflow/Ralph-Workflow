//! Configuration validation and error reporting.
//!
//! This module provides validation for configuration files with:
//! - TOML syntax validation
//! - Type checking (expected vs actual types)
//! - Unknown key detection with typo suggestions (Levenshtein distance)
//! - Multi-file error aggregation
//! - User-friendly error messages
//!
//! ## Architecture
//!
//! The validation process follows these steps:
//! 1. Parse TOML syntax → `ConfigValidationError::TomlSyntax` on failure
//! 2. Detect unknown/deprecated keys → `ConfigValidationError::UnknownKey` + warnings
//! 3. Validate types against schema → `ConfigValidationError::InvalidValue` on mismatch
//!
//! ## Modules
//!
//! - `levenshtein`: String distance calculation for typo suggestions
//! - `keys`: Valid configuration key definitions
//! - `key_detection`: TOML structure traversal for unknown key detection
//! - `error_formatting`: User-friendly error message generation

use std::path::{Path, PathBuf};
use thiserror::Error;

mod error_formatting;
mod key_detection;
mod keys;
mod levenshtein;

// Re-export public API
pub use levenshtein::suggest_key;

/// Configuration validation error.
#[derive(Debug, Clone, Error)]
pub enum ConfigValidationError {
    #[error("TOML syntax error in {file}: {error}")]
    TomlSyntax {
        file: PathBuf,
        error: toml::de::Error,
    },

    #[error("Invalid value in {file} at '{key}': {message}")]
    InvalidValue {
        file: PathBuf,
        key: String,
        message: String,
    },

    #[error("Unknown key in {file}: '{key}'")]
    UnknownKey {
        file: PathBuf,
        key: String,
        suggestion: Option<String>,
    },
}

/// Result of config validation.
/// On success: Ok(warnings) where warnings is a `Vec<String>` of deprecation warnings
/// On failure: Err(errors) where errors is a `Vec<ConfigValidationError>`
pub type ValidationResult = Result<Vec<String>, Vec<ConfigValidationError>>;

/// Validate a config file and collect errors and warnings.
///
/// This validates:
/// - TOML syntax
/// - Type checking against `UnifiedConfig` schema
/// - Unknown keys with typo suggestions
/// - Deprecated keys (returns as warnings, not errors)
///
/// Returns Ok((warnings)) on success with optional deprecation warnings,
/// or Err(errors) on validation failure.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn validate_config_file(
    path: &Path,
    content: &str,
) -> Result<Vec<String>, Vec<ConfigValidationError>> {
    // Step 1: Validate TOML syntax and parse to generic Value for unknown key detection
    let parsed_value: toml::Value = toml::from_str(content).map_err(|e| {
        vec![ConfigValidationError::TomlSyntax {
            file: path.to_path_buf(),
            error: e,
        }]
    })?;

    // Step 2: Detect unknown and deprecated keys by walking the TOML structure
    // This is necessary because #[serde(default)] causes serde to silently ignore unknown fields
    let (unknown_keys, deprecated_keys) =
        key_detection::detect_unknown_and_deprecated_keys(&parsed_value);

    // Collect unknown keys as errors using iterator
    let valid_keys = keys::get_valid_config_keys();
    let unknown_errors: Vec<ConfigValidationError> = unknown_keys
        .iter()
        .map(|(key, location)| ConfigValidationError::UnknownKey {
            file: path.to_path_buf(),
            key: format!("{location}{key}"),
            suggestion: levenshtein::suggest_key(key, &valid_keys),
        })
        .collect();

    // Collect deprecated keys as warnings using iterator
    let deprecation_warnings: Vec<String> = deprecated_keys
        .iter()
        .map(|(key, location)| {
            let full_key = format!("{location}{key}");
            format!(
                "Deprecated key '{}' in {} - this key is no longer used and can be safely removed",
                full_key,
                path.display()
            )
        })
        .collect();

    // Step 3: Validate against UnifiedConfig schema for type checking
    // Unknown keys won't cause deserialization to fail due to #[serde(default)],
    // but we've already detected them in Step 2
    match toml::from_str::<crate::config::unified::UnifiedConfig>(content) {
        Ok(config) => {
            // Check for agent_chain vs agent_chains confusion
            let has_agent_chain = parsed_value.get("agent_chain").is_some();
            let agent_chain_error = has_agent_chain
                .then_some(!config.agent_drains.is_empty() && config.agent_chains.is_empty())
                .and_then(|cond| cond.then_some(ConfigValidationError::InvalidValue {
                    file: path.to_path_buf(),
                    key: "agent_chain".to_string(),
                    message: "found [agent_drains] with singular [agent_chain]; did you mean [agent_chains]? Move retry/backoff settings to [general] (max_retries, retry_delay_ms, backoff_multiplier, max_backoff_ms, max_cycles)".to_string(),
                }));

            let agent_chain_warning = has_agent_chain
                .then_some(config.agent_drains.is_empty() || config.agent_chains.is_empty())
                .and_then(|cond| cond.then_some(format!(
                    "Deprecated section '[agent_chain]' in {} - Ralph will keep legacy role-keyed behavior by adding the default drain bindings automatically. Migrate agent lists to [agent_chains]/[agent_drains] and move retry/backoff settings to [general]",
                    path.display()
                )));

            let has_named_chains = !config.agent_chains.is_empty();
            let has_named_drains = !config.agent_drains.is_empty();
            let has_legacy_role_bindings = config
                .agent_chain
                .as_ref()
                .is_some_and(crate::agents::fallback::FallbackConfig::uses_legacy_role_schema);
            let validate_named_schema_now = (!has_named_chains && !has_named_drains)
                || (has_named_chains && has_named_drains)
                || has_legacy_role_bindings;

            let resolve_error: Option<ConfigValidationError> = validate_named_schema_now
                .then(|| config.resolve_agent_drains_checked())
                .and_then(|result| result.err())
                .map(|message| {
                    let message_string = message.to_string();
                    let key = if message_string.contains("references unknown chain") {
                        message_string
                            .split_whitespace()
                            .next()
                            .map_or_else(|| "agent_drains".to_string(), ToString::to_string)
                    } else if message_string.contains("agent_chain") {
                        "agent_chain".to_string()
                    } else {
                        "agent_drains".to_string()
                    };
                    ConfigValidationError::InvalidValue {
                        file: path.to_path_buf(),
                        key,
                        message: message_string,
                    }
                });

            // Combine all errors
            let schema_errors: Vec<ConfigValidationError> = [agent_chain_error, resolve_error]
                .into_iter()
                .flatten()
                .collect();

            // Combine all warnings
            let schema_warnings: Vec<String> = agent_chain_warning.into_iter().collect();

            let all_errors: Vec<_> = unknown_errors.into_iter().chain(schema_errors).collect();

            let all_warnings: Vec<_> = deprecation_warnings
                .into_iter()
                .chain(schema_warnings)
                .collect();

            if all_errors.is_empty() {
                Ok(all_warnings)
            } else {
                Err(all_errors)
            }
        }
        Err(e) => {
            // TOML is syntactically valid but doesn't match our schema
            // This could be a type error or missing required field
            let error_str = e.to_string();

            // Build schema errors based on error type
            let schema_error: Option<ConfigValidationError> =
                if error_str.contains("missing field") || error_str.contains("invalid type") {
                    Some(ConfigValidationError::InvalidValue {
                        file: path.to_path_buf(),
                        key: error_formatting::extract_key_from_toml_error(&error_str),
                        message: error_formatting::format_invalid_type_message(&error_str),
                    })
                } else {
                    Some(ConfigValidationError::InvalidValue {
                        file: path.to_path_buf(),
                        key: "config".to_string(),
                        message: error_str,
                    })
                };

            let all_errors: Vec<_> = unknown_errors.into_iter().chain(schema_error).collect();

            let all_warnings: Vec<_> = deprecation_warnings.into_iter().collect();

            if all_errors.is_empty() {
                Ok(all_warnings)
            } else {
                Err(all_errors)
            }
        }
    }
}

/// Format validation errors for user display.
#[must_use]
pub fn format_validation_errors(errors: &[ConfigValidationError]) -> String {
    errors
        .iter()
        .map(|error| {
            let error_line = format!("  {error}");
            if let ConfigValidationError::UnknownKey {
                suggestion: Some(s),
                ..
            } = error
            {
                format!("{error_line}\n    Did you mean '{s}'?")
            } else {
                error_line
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validate_config_file_valid_toml() {
        let content = r"
[general]
verbosity = 2
developer_iters = 5
max_retries = 4
retry_delay_ms = 1500
";
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(result.is_ok());
    }

    #[test]
    fn test_validate_config_file_warns_for_legacy_agent_chain_with_migration_message() {
        let content = r#"
[agent_chain]
developer = ["codex"]
max_retries = 5
retry_delay_ms = 2000
"#;

        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(
            result.is_ok(),
            "legacy agent_chain should remain compatible"
        );

        let warnings = result.expect("validation should succeed with warnings");
        assert!(
            warnings
                .iter()
                .any(|warning| warning.contains("Deprecated section '[agent_chain]'")),
            "expected legacy migration warning, got: {warnings:?}"
        );
    }

    #[test]
    fn test_validate_config_file_invalid_toml() {
        let content = r"
[general
verbosity = 2
";
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(result.is_err());

        if let Err(errors) = result {
            assert_eq!(errors.len(), 1);
            match &errors[0] {
                ConfigValidationError::TomlSyntax { file, .. } => {
                    assert_eq!(file, Path::new("test.toml"));
                }
                _ => panic!("Expected TomlSyntax error"),
            }
        }
    }

    #[test]
    fn test_format_validation_errors_with_suggestion() {
        let errors = vec![ConfigValidationError::UnknownKey {
            file: PathBuf::from("test.toml"),
            key: "develper_iters".to_string(),
            suggestion: Some("developer_iters".to_string()),
        }];

        let formatted = format_validation_errors(&errors);
        assert!(formatted.contains("develper_iters"));
        assert!(formatted.contains("Did you mean 'developer_iters'?"));
    }

    #[test]
    fn test_format_validation_errors_without_suggestion() {
        let errors = vec![ConfigValidationError::UnknownKey {
            file: PathBuf::from("test.toml"),
            key: "completely_unknown".to_string(),
            suggestion: None,
        }];

        let formatted = format_validation_errors(&errors);
        assert!(formatted.contains("completely_unknown"));
        assert!(!formatted.contains("Did you mean"));
    }

    #[test]
    fn test_format_validation_errors_multiple() {
        // Create a real TOML parse error
        let toml_error = toml::from_str::<toml::Value>("[invalid\nkey = value").unwrap_err();

        let errors = vec![
            ConfigValidationError::TomlSyntax {
                file: PathBuf::from("global.toml"),
                error: toml_error,
            },
            ConfigValidationError::UnknownKey {
                file: PathBuf::from("local.toml"),
                key: "bad_key".to_string(),
                suggestion: Some("good_key".to_string()),
            },
        ];

        let formatted = format_validation_errors(&errors);
        assert!(formatted.contains("global.toml"));
        assert!(formatted.contains("local.toml"));
        assert!(formatted.contains("Did you mean 'good_key'?"));
    }

    #[test]
    fn test_validate_config_file_unknown_key() {
        let content = r"
[general]
develper_iters = 5
verbosity = 2
";
        let result = validate_config_file(Path::new("test.toml"), content);
        // Unknown keys are now detected via custom validation
        assert!(result.is_err());

        if let Err(errors) = result {
            assert_eq!(errors.len(), 1);
            match &errors[0] {
                ConfigValidationError::UnknownKey {
                    key, suggestion, ..
                } => {
                    assert!(key.contains("develper_iters"));
                    assert_eq!(suggestion.as_ref().unwrap(), "developer_iters");
                }
                _ => panic!("Expected UnknownKey error"),
            }
        }
    }

    #[test]
    fn test_validate_config_file_invalid_type() {
        // This test verifies that type errors during deserialization are caught.
        // When a string is provided where an integer is expected, validation should fail.
        let content = r#"
[general]
developer_iters = "five"
"#;
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(result.is_err(), "Should fail with string instead of int");
    }

    #[test]
    fn test_validate_config_file_valid_with_all_sections() {
        let content = r#"
[general]
verbosity = 2
developer_iters = 5
reviewer_reviews = 2

[ccs]
output_flag = "--output=json"

[agents.claude]
cmd = "claude"

[ccs_aliases]
work = "ccs work"
"#;
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(result.is_ok(), "Valid config with all sections should pass");
    }

    #[test]
    fn test_validate_config_file_empty_file() {
        let content = "";
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(result.is_ok(), "Empty file should use default values");
    }

    #[test]
    fn test_validate_general_retry_keys() {
        let content = r#"
[general]
developer_iters = 5
max_retries = 5
retry_delay_ms = 2000
backoff_multiplier = 2.5
max_backoff_ms = 120000
max_cycles = 5

[agent_chains]
shared_dev = ["claude", "codex"]
shared_review = ["claude"]

[agent_drains]
planning = "shared_dev"
development = "shared_dev"
analysis = "shared_dev"
review = "shared_review"
fix = "shared_review"
commit = "shared_review"
"#;
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(result.is_ok(), "general retry/backoff keys should be valid");
    }

    #[test]
    fn test_validate_general_provider_fallback_key() {
        let content = r#"
[general]

[general.provider_fallback]
opencode = ["-m opencode/glm-4.7-free"]
"#;
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(result.is_ok(), "general.provider_fallback should be valid");
    }

    #[test]
    fn test_validate_agent_chain_with_all_valid_keys() {
        // Legacy [agent_chain] remains accepted with a warning for compatibility.
        let content = r#"
[general]
developer_iters = 5

[agent_chain]
developer = ["claude", "codex"]
reviewer = ["claude"]
commit = ["claude"]
analysis = ["claude"]
max_retries = 5
retry_delay_ms = 2000
backoff_multiplier = 2.5
max_backoff_ms = 120000
max_cycles = 5

[agent_chain.provider_fallback]
opencode = ["-m opencode/glm-4.7-free", "-m opencode/claude-sonnet-4"]
"#;
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(result.is_ok(), "legacy agent_chain should remain valid");
    }

    #[test]
    fn test_validate_agent_chain_commit_key() {
        // The commit key was missing from VALID_AGENT_CHAIN_KEYS
        let content = r#"
[agent_chain]
developer = ["claude"]
commit = ["claude"]
"#;
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(result.is_ok(), "commit key should be valid in agent_chain");
    }

    #[test]
    fn test_validate_agent_chain_analysis_key() {
        // The analysis key was missing from VALID_AGENT_CHAIN_KEYS
        let content = r#"
[agent_chain]
developer = ["claude"]
analysis = ["claude"]
"#;
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(
            result.is_ok(),
            "analysis key should be valid in agent_chain"
        );
    }

    #[test]
    fn test_validate_agent_chain_retry_keys() {
        // These retry/backoff keys were missing from VALID_AGENT_CHAIN_KEYS
        let content = r#"
[agent_chain]
developer = ["claude"]
max_retries = 3
retry_delay_ms = 5000
backoff_multiplier = 1.5
max_backoff_ms = 30000
max_cycles = 2
"#;
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(
            result.is_ok(),
            "retry/backoff keys should be valid in agent_chain"
        );
    }

    #[test]
    fn test_validate_agent_chain_provider_fallback_key() {
        // The provider_fallback nested table was missing from VALID_AGENT_CHAIN_KEYS
        let content = r#"
[agent_chain]
developer = ["opencode"]

[agent_chain.provider_fallback]
opencode = ["-m opencode/glm-4.7-free", "-m opencode/claude-sonnet-4"]
"#;
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(
            result.is_ok(),
            "provider_fallback nested table should be valid in agent_chain"
        );
    }

    #[test]
    fn test_validate_config_file_deprecated_key_warning() {
        let content = r"
[general]
verbosity = 2
auto_rebase = true
max_recovery_attempts = 3
";
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(result.is_ok(), "Deprecated keys should not cause errors");

        if let Ok(warnings) = result {
            assert_eq!(warnings.len(), 2, "Should have 2 deprecation warnings");
            assert!(
                warnings.iter().any(|w| w.contains("auto_rebase")),
                "Should warn about auto_rebase"
            );
            assert!(
                warnings.iter().any(|w| w.contains("max_recovery_attempts")),
                "Should warn about max_recovery_attempts"
            );
        }
    }

    #[test]
    fn test_validate_config_file_no_warnings_without_deprecated() {
        let content = r"
[general]
verbosity = 2
developer_iters = 5
";
        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(result.is_ok(), "Valid config should pass");

        if let Ok(warnings) = result {
            assert_eq!(warnings.len(), 0, "Should have no warnings");
        }
    }

    #[test]
    fn test_validate_config_file_rejects_unknown_agent_drain_binding_target() {
        let content = r#"
[agent_chains]
shared_dev = ["codex"]

[agent_drains]
planning = "missing_chain"
"#;

        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(
            result.is_err(),
            "unknown drain binding target should fail validation"
        );

        let errors = result.expect_err("validation should fail");
        assert!(
            errors.iter().any(|error| matches!(
                error,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key == "agent_drains.planning"
                        && message.contains("missing_chain")
            )),
            "expected invalid drain binding error, got: {errors:?}"
        );
    }

    #[test]
    fn test_validate_config_file_rejects_mixed_legacy_and_named_chain_schema() {
        let content = r#"
[agent_chain]
developer = ["codex"]

[agent_chains]
shared_dev = ["claude"]
"#;

        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(
            result.is_err(),
            "mixing legacy and named chain schema should fail validation"
        );

        let errors = result.expect_err("validation should fail");
        assert!(
            errors.iter().any(|error| matches!(
                error,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key == "agent_chain"
                        && message.contains("agent_chains")
                        && message.contains("agent_drains")
            )),
            "expected mixed schema error, got: {errors:?}"
        );
    }

    #[test]
    fn test_validate_config_file_rejects_incomplete_named_drain_resolution() {
        let content = r#"
[agent_chains]
shared_review = ["claude"]

[agent_drains]
review = "shared_review"
fix = "shared_review"
"#;

        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(
            result.is_err(),
            "incomplete drain coverage should fail validation"
        );

        let errors = result.expect_err("validation should fail");
        assert!(
            errors.iter().any(|error| matches!(
                error,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key == "agent_drains"
                        && message.contains("planning")
                        && message.contains("development")
                        && message.contains("analysis")
            )),
            "expected incomplete drain coverage error, got: {errors:?}"
        );
    }

    #[test]
    fn test_validate_config_file_accepts_commit_and_analysis_derived_from_bound_drains() {
        let content = r#"
[agent_chains]
shared_dev = ["codex"]
shared_review = ["claude"]

[agent_drains]
planning = "shared_dev"
development = "shared_dev"
review = "shared_review"
fix = "shared_review"
"#;

        let result = validate_config_file(Path::new("test.toml"), content);
        assert!(
            result.is_ok(),
            "commit and analysis should derive from existing bound drains"
        );
    }
}
