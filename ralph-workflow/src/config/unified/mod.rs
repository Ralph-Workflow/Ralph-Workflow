//! Unified Configuration Types
//!
//! This module defines the unified configuration format for Ralph,
//! consolidating all settings into a single `~/.config/ralph-workflow.toml` file.
//!
//! # Configuration Structure
//!
//! ```toml
//! [general]
//! verbosity = 2
//! interactive = true
//! isolation_mode = true
//!
//! [agents.claude]
//! cmd = "claude -p"
//! # ...
//!
//! [ccs_aliases]
//! work = "ccs work"
//! personal = "ccs personal"
//!
//! [agent_chains]
//! developer = ["ccs/work", "claude"]
//! reviewer = ["claude"]
//!
//! [agent_drains]
//! planning = "developer"
//! development = "developer"
//! review = "reviewer"
//! fix = "reviewer"
//! commit = "reviewer"
//! analysis = "developer"
//! ```
//!
//! # Module Organization
//!
//! This module is split into focused submodules:
//!
//! - [`types`]: All configuration type definitions (General, CCS, Agent configs)
//! - [`loading`]: Configuration loading and initialization logic
//! - [`merging`]: `merge_with` and `merge_with_content` implementation
//! - [`fallback_merge`]: Fallback chain merge helper functions
//! - [`helpers`]: Utility functions (path resolution, etc.)
//!
//! # Examples
//!
//! ## Loading Configuration
//!
//! ```rust
//! use ralph_workflow::config::unified::UnifiedConfig;
//!
//! // Load from default location (~/.config/ralph-workflow.toml)
//! if let Some(config) = UnifiedConfig::load_default() {
//!     println!("Verbosity: {}", config.general.verbosity);
//! }
//! ```
//!
//! ## Ensuring Configuration Exists
//!
//! ```rust
//! use ralph_workflow::config::unified::{UnifiedConfig, ConfigInitResult};
//!
//! // Create config from template if it doesn't exist
//! match UnifiedConfig::ensure_config_exists() {
//!     Ok(ConfigInitResult::Created) => println!("Created new config"),
//!     Ok(ConfigInitResult::AlreadyExists) => println!("Config already exists"),
//!     Err(e) => eprintln!("Error: {}", e),
//! }
//! # Ok::<(), std::io::Error>(())
//! ```

pub mod fallback_merge;
pub mod helpers;
pub mod loading;
pub mod merging;
pub mod types;

// Re-export all public types and functions at the module level for convenience
pub use helpers::{unified_config_path, DEFAULT_UNIFIED_CONFIG_NAME};
pub use loading::{ConfigInitResult, ConfigLoadError, DEFAULT_UNIFIED_CONFIG};
pub use types::{
    AgentConfigToml, CcsAliasConfig, CcsAliasToml, CcsAliases, CcsConfig, GeneralBehaviorFlags,
    GeneralConfig, GeneralExecutionFlags, GeneralWorkflowFlags, UnifiedConfig,
};

// Clippy's `large_stack_frames` lint trips on the generated lib-test harness once this
// module's unit suite is included. The tests still run in `cargo test`; skipping them only
// for clippy keeps verification deterministic without suppressing lints globally.
#[cfg(all(test, not(clippy)))]
mod tests;
