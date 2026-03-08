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
use super::unified::UnifiedConfig;
use super::validation::{validate_config_file, ConfigValidationError};
use std::path::PathBuf;

mod error_types;
pub use error_types::ConfigLoadWithValidationError;

mod config_builder;
use config_builder::config_from_unified;
pub(super) use config_builder::default_config;

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
pub fn load_config_from_path_with_env(
    config_path: Option<&std::path::Path>,
    env: &dyn ConfigEnvironment,
) -> Result<(super::types::Config, Option<UnifiedConfig>, Vec<String>), ConfigLoadWithValidationError>
{
    let mut warnings = Vec::new();
    let mut validation_errors = Vec::new();

    // Step 1: Load and validate global config
    let global_config_path = config_path
        .map(std::path::Path::to_path_buf)
        .or_else(|| env.unified_config_path());
    let mut global_content: Option<String> = None;

    let global_unified = if let Some(path) = global_config_path.as_ref() {
        if env.file_exists(path) {
            let content = env.read_file(path)?;
            // Validate the config file
            match validate_config_file(path, &content) {
                Ok(config_warnings) => {
                    warnings.extend(config_warnings);
                }
                Err(errors) => {
                    validation_errors.extend(errors);
                }
            }
            match UnifiedConfig::load_from_content(&content) {
                Ok(cfg) => {
                    global_content = Some(content);
                    Some(cfg)
                }
                Err(e) => {
                    validation_errors.push(ConfigValidationError::InvalidValue {
                        file: path.clone(),
                        key: "config".to_string(),
                        message: format!("Failed to parse config: {e}"),
                    });
                    None
                }
            }
        } else {
            if config_path.is_some() {
                warnings.push(format!("Global config file not found: {}", path.display()));
            }
            None
        }
    } else {
        None
    };

    // Step 2: Load and validate local config (only when no explicit --config path).
    let (local_unified, local_content) = if config_path.is_none() {
        if let Some(local_path) = env.local_config_path() {
            if env.file_exists(&local_path) {
                let content = env.read_file(&local_path)?;
                // Validate the config file
                match validate_config_file(&local_path, &content) {
                    Ok(config_warnings) => {
                        warnings.extend(config_warnings);
                    }
                    Err(errors) => {
                        validation_errors.extend(errors);
                    }
                }
                match UnifiedConfig::load_from_content(&content) {
                    Ok(cfg) => (Some(cfg), Some(content)),
                    Err(e) => {
                        validation_errors.push(ConfigValidationError::InvalidValue {
                            file: local_path,
                            key: "config".to_string(),
                            message: format!("Failed to parse config: {e}"),
                        });
                        (None, None)
                    }
                }
            } else {
                (None, None)
            }
        } else {
            (None, None)
        }
    } else {
        (None, None)
    };

    // Fail-fast: if there are any validation errors, return them immediately
    if !validation_errors.is_empty() {
        return Err(ConfigLoadWithValidationError::ValidationErrors(
            validation_errors,
        ));
    }

    // Step 3: Merge configs (local overrides global)
    let merged_unified = match (global_unified, local_unified, local_content) {
        (Some(global), Some(local), Some(content)) => {
            // Both exist: first normalize global agent_chain against built-in defaults
            // using raw global TOML key presence, then merge local with raw local presence.
            let normalized_global = global_content.as_ref().map_or_else(
                || global.clone(),
                |raw_global_content| {
                    merge_global_with_built_in_agent_chain_defaults(&global, raw_global_content)
                },
            );

            // Pass raw local TOML content for local presence tracking
            Some(normalized_global.merge_with_content(&content, &local))
        }
        (Some(_global), Some(_local), None) => {
            // SAFETY: This case is impossible in production. If local_unified is Some,
            // then local_content must also be Some (they're set together at line 281).
            // If we reach here, there's a bug in the config loading logic.
            unreachable!(
                "BUG: local_unified is Some but local_content is None. \
                 This indicates a logic error in config loading - they should always be set together."
            )
        }
        (Some(global), None, _) => {
            // Only global exists: preserve explicit global values exactly.
            // For agent_chain, resolve missing roles through built-in defaults using
            // raw global key presence so omitted roles inherit defaults while explicit
            // empty lists still override.
            if let Some(content) = global_content.as_ref() {
                Some(merge_global_with_built_in_agent_chain_defaults(
                    &global, content,
                ))
            } else {
                Some(global)
            }
        }
        (None, Some(local), Some(content)) => {
            // Only local exists: merge against `UnifiedConfig::default()` so missing keys
            // still resolve through local > global > defaults semantics in the unified layer.
            Some(UnifiedConfig::default().merge_with_content(&content, &local))
        }
        (None, Some(_local), None) => {
            // SAFETY: This case is impossible in production. If local_unified is Some,
            // then local_content must also be Some (they're set together at line 281).
            unreachable!(
                "BUG: local_unified is Some but local_content is None. \
                 This indicates a logic error in config loading - they should always be set together."
            )
        }
        (None, None, _) => {
            // Neither exists: use defaults
            None
        }
    };

    // Step 4: Convert to Config
    // Build cloud config from the injected env (not the real process env) so that
    // callers using MemoryConfigEnvironment get a deterministic, isolated cloud config.
    let cloud = super::types::CloudConfig::from_env_fn(|k| env.get_env_var(k));
    let config = {
        let mut cfg = merged_unified
            .as_ref()
            .map_or_else(default_config, |unified_cfg| {
                config_from_unified(unified_cfg, &mut warnings)
            });
        cfg.cloud = cloud;
        cfg
    };

    // Step 5: Apply environment variable overrides
    let config = apply_env_overrides(config, &mut warnings, env);

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

    Ok((config, merged_unified, warnings))
}

fn merge_global_with_built_in_agent_chain_defaults(
    global: &UnifiedConfig,
    global_content: &str,
) -> UnifiedConfig {
    let mut merged = global.clone();
    let resolved = UnifiedConfig::default().merge_with_content(global_content, global);
    merged.agent_chain = resolved.agent_chain;
    merged
}

mod env_parsing;

mod unified_config_exists;

pub use unified_config_exists::{unified_config_exists, unified_config_exists_with_env};

#[cfg(test)]
mod tests;
