//! Configuration loading and initialization.
//!
//! This module provides functions for loading and initializing Ralph's unified configuration.
//!
//! # Loading Strategy
//!
//! Configuration loading supports both production and testing scenarios:
//!
//! - **Production**: Uses `load_default()` which reads from `~/.config/ralph-workflow.toml`
//! - **Testing**: Uses `load_with_env()` with a `ConfigEnvironment` trait for test isolation
//!
//! # Initialization
//!
//! Ralph can automatically create a default configuration file if none exists:
//!
//! ```rust
//! use ralph_workflow::config::unified::UnifiedConfig;
//!
//! // Ensure config exists, creating it if needed
//! let result = UnifiedConfig::ensure_config_exists()?;
//!
//! // Load the config
//! let config = UnifiedConfig::load_default()
//!     .expect("Config should exist after ensure_config_exists");
//! # Ok::<(), std::io::Error>(())
//! ```

use super::types::UnifiedConfig;

/// Result of config initialization.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConfigInitResult {
    /// Config was created successfully.
    Created,
    /// Config already exists.
    AlreadyExists,
}

/// Error type for unified config loading.
#[derive(Debug, thiserror::Error)]
pub enum ConfigLoadError {
    #[error("Failed to read config file: {0}")]
    Io(#[from] std::io::Error),
    #[error("Failed to parse TOML: {0}")]
    Toml(#[from] toml::de::Error),
}

/// Default unified config template embedded at compile time.
pub const DEFAULT_UNIFIED_CONFIG: &str = include_str!("../../../examples/ralph-workflow.toml");

impl UnifiedConfig {
    /// Load unified configuration from the default path.
    ///
    /// Returns None if the file doesn't exist.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use ralph_workflow::config::unified::UnifiedConfig;
    ///
    /// if let Some(config) = UnifiedConfig::load_default() {
    ///     println!("Verbosity level: {}", config.general.verbosity);
    /// }
    /// ```
    #[must_use]
    pub fn load_default() -> Option<Self> {
        Self::load_with_env(&super::super::path_resolver::RealConfigEnvironment)
    }

    /// Load unified configuration using a `ConfigEnvironment`.
    ///
    /// This is the testable version of `load_default`. It reads from the
    /// unified config path as determined by the environment.
    ///
    /// Returns None if no config path is available or the file doesn't exist.
    pub fn load_with_env(env: &dyn super::super::path_resolver::ConfigEnvironment) -> Option<Self> {
        env.unified_config_path().and_then(|path| {
            if env.file_exists(&path) {
                Self::load_from_path_with_env(&path, env).ok()
            } else {
                None
            }
        })
    }

    /// Load unified configuration from a specific path using a `ConfigEnvironment`.
    ///
    /// This is the testable version of `load_from_path`.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn load_from_path_with_env(
        path: &std::path::Path,
        env: &dyn super::super::path_resolver::ConfigEnvironment,
    ) -> Result<Self, ConfigLoadError> {
        let contents = env.read_file(path)?;
        let config: Self = toml::from_str(&contents)?;
        Ok(config)
    }

    /// Load unified configuration from pre-read content.
    ///
    /// This avoids re-reading the file when content is already available.
    /// The path is used only for error messages.
    ///
    /// # Arguments
    ///
    /// * `content` - The raw TOML content string
    ///
    /// # Errors
    ///
    /// Returns an error if the TOML syntax is invalid or required fields are missing.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use ralph_workflow::config::unified::UnifiedConfig;
    ///
    /// let toml_content = r#"
    ///     [general]
    ///     verbosity = 3
    /// "#;
    ///
    /// let config = UnifiedConfig::load_from_content(toml_content)?;
    /// assert_eq!(config.general.verbosity, 3);
    /// # Ok::<(), Box<dyn std::error::Error>>(())
    /// ```
    pub fn load_from_content(content: &str) -> Result<Self, ConfigLoadError> {
        let config: Self = toml::from_str(content)?;
        Ok(config)
    }

    /// Ensure unified config file exists, creating it from template if needed.
    ///
    /// This creates `~/.config/ralph-workflow.toml` with the default template
    /// if it doesn't already exist.
    ///
    /// # Returns
    ///
    /// - `Created` if the config file was created
    /// - `AlreadyExists` if the config file already existed
    ///
    /// # Errors
    ///
    /// Returns an error if:
    /// - The home directory cannot be determined
    /// - The config file cannot be written
    ///
    /// # Examples
    ///
    /// ```rust
    /// use ralph_workflow::config::unified::{UnifiedConfig, ConfigInitResult};
    ///
    /// match UnifiedConfig::ensure_config_exists() {
    ///     Ok(ConfigInitResult::Created) => println!("Created new config"),
    ///     Ok(ConfigInitResult::AlreadyExists) => println!("Config already exists"),
    ///     Err(e) => eprintln!("Failed to create config: {}", e),
    /// }
    /// # Ok::<(), std::io::Error>(())
    /// ```
    pub fn ensure_config_exists() -> std::io::Result<ConfigInitResult> {
        Self::ensure_config_exists_with_env(&super::super::path_resolver::RealConfigEnvironment)
    }

    /// Ensure unified config file exists using a `ConfigEnvironment`.
    ///
    /// This is the testable version of `ensure_config_exists`.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn ensure_config_exists_with_env(
        env: &dyn super::super::path_resolver::ConfigEnvironment,
    ) -> std::io::Result<ConfigInitResult> {
        let Some(path) = env.unified_config_path() else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                "Cannot determine config directory (no home directory)",
            ));
        };

        Self::ensure_config_exists_at_with_env(&path, env)
    }

    /// Ensure a config file exists at the specified path.
    ///
    /// This is useful for custom config file locations or testing.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn ensure_config_exists_at(path: &std::path::Path) -> std::io::Result<ConfigInitResult> {
        Self::ensure_config_exists_at_with_env(
            path,
            &super::super::path_resolver::RealConfigEnvironment,
        )
    }

    /// Ensure a config file exists at the specified path using a `ConfigEnvironment`.
    ///
    /// This is the testable version of `ensure_config_exists_at`.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn ensure_config_exists_at_with_env(
        path: &std::path::Path,
        env: &dyn super::super::path_resolver::ConfigEnvironment,
    ) -> std::io::Result<ConfigInitResult> {
        if env.file_exists(path) {
            return Ok(ConfigInitResult::AlreadyExists);
        }

        // Write the default template (write_file creates parent directories)
        env.write_file(path, DEFAULT_UNIFIED_CONFIG)?;

        Ok(ConfigInitResult::Created)
    }
}
