//! Agent configuration types and TOML parsing.
//!
//! This module provides types for loading and managing agent configurations
//! from TOML files, including support for global and per-project configs.

#[path = "config/file.rs"]
mod file;
#[path = "config/types.rs"]
mod types;

/// Default agents.toml template embedded at compile time.
pub(crate) const DEFAULT_AGENTS_TOML: &str = include_str!("../../examples/agents.toml");

pub use file::{AgentConfigError, AgentsConfigFile, ConfigInitResult};
pub use types::{should_use_yolo_mode, AgentConfig, AgentConfigBuilder, ConfigSource};
