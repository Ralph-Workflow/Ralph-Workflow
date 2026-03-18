//! Configuration Module
//!
//! Handles environment variables and configuration for Ralph.
//!
//! # Key Types
//!
//! - [`Config`] - Runtime configuration used during pipeline execution
//! - [`Verbosity`] - Output verbosity levels (Quiet, Normal, Verbose, Full, Debug)
//! - [`ReviewDepth`] - Review thoroughness (Standard, Comprehensive, Security, Incremental)
//! - [`UnifiedConfig`] - Full configuration file representation
//!
//! # Configuration Sources
//!
//! Ralph configuration is loaded from (in order of priority):
//!
//! 1. `~/.config/ralph-workflow.toml` (global config)
//! 2. `.agent/ralph-workflow.toml` (local config, overrides global)
//! 3. Environment variables (`RALPH_*`) as overrides
//! 4. CLI arguments (final override)
//!
//! # Module Structure
//!
//! - [`types`] - Core configuration types (Config, `ReviewDepth`, Verbosity)
//! - [`cloud`] - Cloud and git remote configuration types
//! - [`truncation`] - Truncation limits for verbosity levels
//! - [`parser`] - Environment variable parsing (legacy)
//! - [`unified`] - Unified configuration format types
//! - [`loader`] - Unified configuration loader with env overrides
//! - [`path_resolver`] - Configuration path resolution with dependency injection

// pub mod cloud; // Disabled due to module conflict - cloud.rs is used directly
pub mod boundary;
#[path = "cloud.rs"]
mod cloud;
pub mod loader;
pub mod parser;
pub mod path_resolver;
pub mod truncation;
pub mod types;
pub mod unified;
pub mod validation;

// Re-export cloud types from the private module
pub use cloud::{
    CloudConfig, CloudStateConfig, GitAuthMethod, GitAuthStateMethod, GitRemoteConfig,
    GitRemoteStateConfig,
};
pub use types::{Config, ReviewDepth, Verbosity};

// Re-export unified config types for --init-global handling
pub use unified::{
    unified_config_path, CcsAliasConfig, CcsConfig, ConfigInitResult as UnifiedConfigInitResult,
    UnifiedConfig,
};

// Re-export config environment types for dependency injection
pub use path_resolver::{ConfigEnvironment, MemoryConfigEnvironment, RealConfigEnvironment};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_verbosity_from_u8() {
        assert_eq!(Verbosity::from(0), Verbosity::Quiet);
        assert_eq!(Verbosity::from(1), Verbosity::Normal);
        assert_eq!(Verbosity::from(2), Verbosity::Verbose);
        assert_eq!(Verbosity::from(3), Verbosity::Full);
        assert_eq!(Verbosity::from(4), Verbosity::Debug);
        assert_eq!(Verbosity::from(100), Verbosity::Debug);
    }

    #[test]
    fn test_truncate_limits() {
        // Quiet has reduced limits
        assert_eq!(Verbosity::Quiet.truncate_limit("text"), 80);
        assert_eq!(Verbosity::Quiet.truncate_limit("tool_input"), 40);

        // Normal has conservative limits for manageable output
        assert_eq!(Verbosity::Normal.truncate_limit("text"), 1000);
        assert_eq!(Verbosity::Normal.truncate_limit("tool_input"), 300);
        assert_eq!(Verbosity::Normal.truncate_limit("tool_result"), 500);

        // Verbose (default) has conservative limits for reasonable output
        assert_eq!(Verbosity::Verbose.truncate_limit("text"), 2000);
        assert_eq!(Verbosity::Verbose.truncate_limit("tool_input"), 300);
        assert_eq!(Verbosity::Verbose.truncate_limit("tool_result"), 500);

        // Full and Debug have unlimited
        assert_eq!(Verbosity::Full.truncate_limit("text"), 999_999);
        assert_eq!(Verbosity::Debug.truncate_limit("text"), 999_999);
    }

    #[test]
    fn test_verbosity_helpers() {
        assert!(!Verbosity::Quiet.is_debug());
        assert!(!Verbosity::Normal.is_debug());
        assert!(!Verbosity::Verbose.is_debug());
        assert!(!Verbosity::Full.is_debug());
        assert!(Verbosity::Debug.is_debug());

        assert!(!Verbosity::Quiet.is_verbose());
        assert!(!Verbosity::Normal.is_verbose());
        assert!(Verbosity::Verbose.is_verbose());
        assert!(Verbosity::Full.is_verbose());
        assert!(Verbosity::Debug.is_verbose());

        // show_tool_input: true for Normal and above, false for Quiet
        assert!(!Verbosity::Quiet.show_tool_input());
        assert!(Verbosity::Normal.show_tool_input());
        assert!(Verbosity::Verbose.show_tool_input());
        assert!(Verbosity::Full.show_tool_input());
        assert!(Verbosity::Debug.show_tool_input());
    }

    #[test]
    fn test_review_depth_from_str() {
        // Standard aliases
        assert_eq!(
            ReviewDepth::from_str("standard"),
            Some(ReviewDepth::Standard)
        );
        assert_eq!(
            ReviewDepth::from_str("default"),
            Some(ReviewDepth::Standard)
        );
        assert_eq!(ReviewDepth::from_str("normal"), Some(ReviewDepth::Standard));

        // Comprehensive aliases
        assert_eq!(
            ReviewDepth::from_str("comprehensive"),
            Some(ReviewDepth::Comprehensive)
        );
        assert_eq!(
            ReviewDepth::from_str("thorough"),
            Some(ReviewDepth::Comprehensive)
        );
        assert_eq!(
            ReviewDepth::from_str("full"),
            Some(ReviewDepth::Comprehensive)
        );

        // Security aliases
        assert_eq!(
            ReviewDepth::from_str("security"),
            Some(ReviewDepth::Security)
        );
        assert_eq!(ReviewDepth::from_str("secure"), Some(ReviewDepth::Security));
        assert_eq!(
            ReviewDepth::from_str("security-focused"),
            Some(ReviewDepth::Security)
        );

        // Incremental aliases
        assert_eq!(
            ReviewDepth::from_str("incremental"),
            Some(ReviewDepth::Incremental)
        );
        assert_eq!(
            ReviewDepth::from_str("diff"),
            Some(ReviewDepth::Incremental)
        );
        assert_eq!(
            ReviewDepth::from_str("changed"),
            Some(ReviewDepth::Incremental)
        );

        // Case insensitivity
        assert_eq!(
            ReviewDepth::from_str("SECURITY"),
            Some(ReviewDepth::Security)
        );
        assert_eq!(
            ReviewDepth::from_str("Comprehensive"),
            Some(ReviewDepth::Comprehensive)
        );

        // Invalid values
        assert_eq!(ReviewDepth::from_str("invalid"), None);
        assert_eq!(ReviewDepth::from_str(""), None);
    }

    #[test]
    fn test_review_depth_default() {
        assert_eq!(ReviewDepth::default(), ReviewDepth::Standard);
    }

    #[test]
    fn test_review_depth_description() {
        assert!(ReviewDepth::Standard.description().contains("Balanced"));
        assert!(ReviewDepth::Comprehensive
            .description()
            .contains("In-depth"));
        assert!(ReviewDepth::Security.description().contains("OWASP"));
        assert!(ReviewDepth::Incremental.description().contains("git diff"));
    }
}
