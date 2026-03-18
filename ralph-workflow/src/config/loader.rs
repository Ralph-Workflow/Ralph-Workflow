//! Unified Configuration Loader
//!
//! This module handles loading configuration from the unified config file
//! at `~/.config/ralph-workflow.toml`, with environment variable overrides.
//!
//! # Configuration Priority
//!
//! 1. **Explicit config path**: `--config PATH` (if provided)
//! 2. **Global config**: `~/.config/ralph-workflow.toml` (when no explicit path)
//! 3. **Local config**: `.agent/ralph-workflow.toml` (overrides global, only when no explicit path)
//! 4. **Override layer**: Environment variables (RALPH_*)
//! 5. **CLI arguments**: Final override (handled at CLI layer)
//!
//! # Legacy Configs
//!
//! Legacy config discovery is intentionally not supported. Only the unified
//! config path is consulted, and missing config files fall back to defaults.
//!
//! # Fail-Fast Validation
//!
//! Ralph validates ALL config files before starting the pipeline. Invalid TOML,
//! type mismatches, or unknown keys will cause Ralph to refuse to start with
//! a clear error message. This is not optional - config validation runs on
//! every startup before any other CLI operation.
use super::path_resolver::ConfigEnvironment;
use super::types::Config;
use super::unified::UnifiedConfig;
use super::validation::{validate_config_file, ConfigValidationError};
use std::path::PathBuf;

mod error_types;
pub use error_types::ConfigLoadWithValidationError;

mod config_builder;
use config_builder::config_from_unified;
pub(super) use config_builder::default_config;
use config_builder::ConfigConversionResult;

mod env_overrides;
pub(super) use env_overrides::apply_env_overrides;

/// Load configuration with the unified approach.
///
/// This function loads configuration from the unified config file
/// (`~/.config/ralph-workflow.toml`) and applies environment variable overrides.
///
/// # Returns
///
/// Returns a tuple of `(Config, Vec<String>)` where the second element
/// contains any deprecation warnings to be displayed to the user.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn load_config(
) -> Result<(super::types::Config, Option<UnifiedConfig>, Vec<String>), ConfigLoadWithValidationError>
{
    load_config_from_path(None)
}

/// Load configuration from a specific path or the default location.
///
/// If `config_path` is provided, loads from that file.
/// Otherwise, loads from the default unified config location.
///
/// # Arguments
///
/// * `config_path` - Optional path to a config file. If None, uses the default location.
///
/// # Returns
///
/// Returns a tuple of `(Config, Option<UnifiedConfig>, Vec<String>)` where the last element
/// contains any deprecation warnings to be displayed to the user.
///
/// # Panics
///
/// This function does not panic. Validation errors are returned to the caller.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn load_config_from_path(
    config_path: Option<&std::path::Path>,
) -> Result<(super::types::Config, Option<UnifiedConfig>, Vec<String>), ConfigLoadWithValidationError>
{
    load_config_from_path_with_env(config_path, &super::path_resolver::RealConfigEnvironment)
}

/// Load configuration from a specific path or the default location using a [`ConfigEnvironment`].
///
/// This is the testable version of [`load_config_from_path`]. It uses the provided
/// environment for all filesystem operations.
///
/// # Arguments
///
/// * `config_path` - Optional path to a config file. If None, uses the environment's default.
/// * `env` - The configuration environment to use for filesystem operations.
///
/// # Returns
///
/// Returns a tuple of `(Config, Option<UnifiedConfig>, Vec<String>)` where the last element
/// contains any deprecation warnings to be displayed to the user.
///
/// # Errors
///
/// Returns `Err(ConfigLoadWithValidationError)` if any config file has validation errors
/// (invalid TOML, type mismatches, unknown keys). Per requirements, Ralph refuses to start
/// if ANY config file has errors.
struct GlobalLoadResult {
    unified: Option<UnifiedConfig>,
    content: Option<String>,
    warnings: Vec<String>,
    validation_errors: Vec<ConfigValidationError>,
}

fn load_global_config(
    config_path: Option<&std::path::Path>,
    env: &dyn ConfigEnvironment,
) -> GlobalLoadResult {
    let global_config_path = config_path
        .map(std::path::Path::to_path_buf)
        .or_else(|| env.unified_config_path());

    if let Some(path) = global_config_path.as_ref() {
        if env.file_exists(path) {
            let content = match env.read_file(path) {
                Ok(c) => c,
                Err(e) => {
                    return GlobalLoadResult {
                        unified: None,
                        content: None,
                        warnings: Vec::new(),
                        validation_errors: vec![ConfigValidationError::InvalidValue {
                            file: path.clone(),
                            key: "config".to_string(),
                            message: format!("Failed to read config file: {e}"),
                        }],
                    };
                }
            };

            let (warnings, validation_errors) = match validate_config_file(path, &content) {
                Ok(config_warnings) => (config_warnings, Vec::new()),
                Err(errors) => (Vec::new(), errors),
            };

            let (unified, more_errors) = match UnifiedConfig::load_from_content(&content) {
                Ok(cfg) => (Some(cfg), Vec::new()),
                Err(e) => (
                    None,
                    vec![ConfigValidationError::InvalidValue {
                        file: path.clone(),
                        key: "config".to_string(),
                        message: format!("Failed to parse config: {e}"),
                    }],
                ),
            };

            return GlobalLoadResult {
                unified,
                content: Some(content),
                warnings,
                validation_errors: [validation_errors, more_errors].concat(),
            };
        } else if config_path.is_some() {
            return GlobalLoadResult {
                unified: None,
                content: None,
                warnings: vec![format!("Global config file not found: {}", path.display())],
                validation_errors: Vec::new(),
            };
        }
    }

    GlobalLoadResult::default()
}

impl Default for GlobalLoadResult {
    fn default() -> Self {
        Self {
            unified: None,
            content: None,
            warnings: Vec::new(),
            validation_errors: Vec::new(),
        }
    }
}

struct LocalLoadResult {
    unified: Option<UnifiedConfig>,
    content: Option<String>,
    warnings: Vec<String>,
    validation_errors: Vec<ConfigValidationError>,
}

impl Default for LocalLoadResult {
    fn default() -> Self {
        Self {
            unified: None,
            content: None,
            warnings: Vec::new(),
            validation_errors: Vec::new(),
        }
    }
}

fn load_local_config(env: &dyn ConfigEnvironment) -> LocalLoadResult {
    if let Some(local_path) = env.local_config_path() {
        if env.file_exists(&local_path) {
            let content = match env.read_file(&local_path) {
                Ok(c) => c,
                Err(e) => {
                    return LocalLoadResult {
                        unified: None,
                        content: None,
                        warnings: Vec::new(),
                        validation_errors: vec![ConfigValidationError::InvalidValue {
                            file: local_path,
                            key: "config".to_string(),
                            message: format!("Failed to read config file: {e}"),
                        }],
                    };
                }
            };

            let (warnings, validation_errors) = match validate_config_file(&local_path, &content) {
                Ok(config_warnings) => (config_warnings, Vec::new()),
                Err(errors) => (Vec::new(), errors),
            };

            let (unified, more_errors) = match UnifiedConfig::load_from_content(&content) {
                Ok(cfg) => (Some(cfg), Vec::new()),
                Err(e) => (
                    None,
                    vec![ConfigValidationError::InvalidValue {
                        file: local_path,
                        key: "config".to_string(),
                        message: format!("Failed to parse config: {e}"),
                    }],
                ),
            };

            return LocalLoadResult {
                unified,
                content: Some(content),
                warnings,
                validation_errors: [validation_errors, more_errors].concat(),
            };
        }
    }

    LocalLoadResult::default()
}

pub fn load_config_from_path_with_env(
    config_path: Option<&std::path::Path>,
    env: &dyn ConfigEnvironment,
) -> Result<(super::types::Config, Option<UnifiedConfig>, Vec<String>), ConfigLoadWithValidationError>
{
    // Step 1: Load global config
    let global = load_global_config(config_path, env);

    // Step 2: Load local config (only when no explicit --config path).
    let local = if config_path.is_none() {
        load_local_config(env)
    } else {
        LocalLoadResult::default()
    };

    // Combine warnings and validation errors
    let initial_warnings: Vec<String> = global.warnings.into_iter().chain(local.warnings).collect();
    let all_validation_errors = [global.validation_errors, local.validation_errors].concat();

    // Fail-fast: if there are any validation errors, return them immediately
    if !all_validation_errors.is_empty() {
        return Err(ConfigLoadWithValidationError::ValidationErrors(
            all_validation_errors,
        ));
    }

    // Step 3: Merge configs (local overrides global)
    let merged_unified = match (global.unified, global.content, local.unified, local.content) {
        (Some(global_cfg), Some(global_raw_content), Some(local_cfg), Some(local_content)) => {
            let normalized_global =
                merge_global_with_built_in_agent_chain_defaults(&global_cfg, &global_raw_content);
            Some(normalized_global.merge_with_content(&local_content, &local_cfg))
        }
        (Some(global_cfg), Some(global_raw_content), None, _) => Some(
            merge_global_with_built_in_agent_chain_defaults(&global_cfg, &global_raw_content),
        ),
        (Some(global_cfg), None, None, _) => Some(global_cfg),
        (None, _, Some(local_cfg), Some(local_content)) => {
            Some(UnifiedConfig::default().merge_with_content(&local_content, &local_cfg))
        }
        (None, _, None, _) => None,
        _ => unreachable!("Unexpected config loading state"),
    };

    if let Some(unified_cfg) = merged_unified.as_ref() {
        if let Err(message) = unified_cfg.resolve_agent_drains_checked() {
            let key = if message.contains("references unknown chain") {
                message
                    .split_whitespace()
                    .next()
                    .map_or_else(|| "agent_drains".to_string(), ToString::to_string)
            } else if message.contains("cannot be combined") {
                "agent_chain".to_string()
            } else {
                "agent_drains".to_string()
            };

            return Err(ConfigLoadWithValidationError::ValidationErrors(vec![
                ConfigValidationError::InvalidValue {
                    file: PathBuf::from("<merged-config>"),
                    key,
                    message,
                },
            ]));
        }
    }

    // Step 4: Convert to Config
    let cloud = super::types::CloudConfig::from_env_fn(|k| env.get_env_var(k));
    let conversion_result = merged_unified.as_ref().map_or_else(
        || ConfigConversionResult::new(default_config()),
        config_from_unified,
    );
    let config = Config {
        cloud,
        ..conversion_result.config
    };

    // Step 5: Apply environment variable overrides
    let override_result = apply_env_overrides(config, env);
    let config = override_result.config;

    // Step 6: Validate cloud configuration (fail-fast)
    if let Err(e) = config.cloud.validate() {
        return Err(ConfigLoadWithValidationError::ValidationErrors(vec![
            ConfigValidationError::InvalidValue {
                file: PathBuf::from("<environment>"),
                key: "cloud".to_string(),
                message: e,
            },
        ]));
    }

    // Combine all warnings from all sources
    let all_warnings: Vec<String> = initial_warnings
        .into_iter()
        .chain(conversion_result.warnings)
        .chain(override_result.warnings)
        .collect();

    Ok((config, merged_unified, all_warnings))
}

fn merge_global_with_built_in_agent_chain_defaults(
    global: &UnifiedConfig,
    global_content: &str,
) -> UnifiedConfig {
    let resolved = UnifiedConfig::default().merge_with_content(global_content, global);
    UnifiedConfig {
        agent_chain: resolved.agent_chain,
        ..global.clone()
    }
}

mod env_parsing;

mod unified_config_exists;

pub use unified_config_exists::{unified_config_exists, unified_config_exists_with_env};

#[cfg(test)]
mod tests;
